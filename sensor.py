"""Sensor platform for China Gas integration."""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_IS_PREPAID,
    CONF_LADDER_LEVEL_1,
    CONF_LADDER_LEVEL_2,
    CONF_LADDER_PRICE_1,
    CONF_LADDER_PRICE_2,
    CONF_LADDER_PRICE_3,
    CONF_YEAR_LADDER_START,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_LADDER_LEVEL_1 = 480
DEFAULT_LADDER_LEVEL_2 = 660
DEFAULT_LADDER_PRICE_1 = 2.18
DEFAULT_LADDER_PRICE_2 = 2.62
DEFAULT_LADDER_PRICE_3 = 3.27
DEFAULT_YEAR_LADDER_START = "0101"

API_BASE = "https://zrds.95007.com"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the China Gas sensor platform."""
    config = {**entry.data, **entry.options}

    coordinator = ChinaGasCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()

    sensor = ChinaGasSensor(coordinator, config)

    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator
    hass.data[DOMAIN][entry.entry_id]["entities"] = [sensor]

    async_add_entities([sensor], True)


class ChinaGasCoordinator(DataUpdateCoordinator):
    """Coordinator for China Gas data."""

    def __init__(self, hass: HomeAssistant, config: dict):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=2),
        )
        self.config = config
        self.last_update_time = datetime.now()
        self.data = None

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            user_id = self.config["user_id"]
            access_token = self.config["access_token"]
            cust_code = self.config.get("cust_code", "")
            cust_name = self.config.get("cust_name", "")

            headers = {
                "userId": user_id,
                "accessToken": access_token,
                "x-mas-app-info": self.config.get("x_mas_app_info", ""),
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "accessFrom": "yphpaymp",
                "platform": "mp-weixin",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.73(0x18004921) NetType/WIFI Language/zh_CN",
                "Referer": "https://servicewechat.com/wx2082cbdc25b3b8e6/110/page-frame.html",
            }

            # Signature 为占位值，API 不做校验
            base_ts = int(time.time() * 1000)
            fake_sig = "fake1234567890"

            # 1. 获取账户信息
            account_body = f"userId={user_id}&state=1&timeStamp={base_ts}&signature={fake_sig}"
            account_data = await self._make_request(
                f"{API_BASE}/crm_controller/user/getBindGasCustList",
                headers,
                account_body,
            )

            # 间隔 5 秒避免频率限制
            await asyncio.sleep(5)

            # 2. 获取 IC 卡信息
            ic_body = f"custCode={cust_code}&custName={cust_name}&timeStamp={base_ts + 2000}&signature={fake_sig}"
            ic_data = await self._make_request(
                f"{API_BASE}/crm_controller/user/findCustInfoByCustCodeAndCustName",
                headers,
                ic_body,
            )

            await asyncio.sleep(5)

            # 3. 获取缴费记录
            start_time = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
            end_time = datetime.now().strftime("%Y%m%d")
            payment_body = f"custCode={cust_code}&startTime={start_time}&endTime={end_time}&timeStamp={base_ts + 4000}&signature={fake_sig}"
            payment_data = await self._make_request(
                f"{API_BASE}/crm_controller/payfee/getPaymentList",
                headers,
                payment_body,
            )

            if not ic_data and not payment_data:
                _LOGGER.warning("未获取到燃气数据")
                return self.data or {}

            processed = self._process_data(account_data, ic_data, payment_data)
            self.data = processed
            self.last_update_time = datetime.now()
            return self.data

        except (aiohttp.ClientError, ValueError, KeyError, TypeError) as ex:
            _LOGGER.error("更新燃气数据失败: %s", ex)
            raise UpdateFailed(f"Error updating gas data: {ex}")

    async def _make_request(self, url: str, headers: dict, body: str) -> dict[str, Any] | None:
        """Make HTTP request."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=body.encode('utf-8') if isinstance(body, str) else body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        response_text = await response.text()
                        _LOGGER.error("请求失败 status=%s url=%s response=%s", response.status, url, response_text)
                        return None
        except aiohttp.ClientError as err:
            _LOGGER.error("请求错误: %s url=%s", err, url)
            return None

    def _process_data(self, account_data, ic_data, payment_data):
        """Process raw API data into standardized format."""
        try:
            result = {
                "balance": 0,
                "card_no": "",
                "meter_no": "",
                "cust_name": "",
                "address": "",
                "last_record": 0,
                "last_record_time": "",
                "owe_money": 0,
                "purch_times": 0,
                "max_gas": 0,
                "last_payment": None,
                "last_purchase": None,
                "monthly_usage": {},
                "daily_avg": {},
                "daily_avg_recent_3m": 0,
                "daily_avg_recent_12m": 0,
                "daily_avg_year": 0,
                "monthly_cost": {},
                "f_gas_total": {},
                "e_gas_total": {},
                "daylist": [],
                "total_purchased": 0,
                "total_spent": 0,
                "year_accumulated": 0,
                "date": "",
            }

            if account_data and account_data.get("data"):
                account = account_data["data"][0] if isinstance(account_data["data"], list) else account_data["data"]
                result["cust_name"] = account.get("custName", "")
                result["address"] = account.get("address", "")

            if ic_data and ic_data.get("data"):
                ic = ic_data["data"]
                result["balance"] = float(ic.get("qtyBalance", 0))
                result["card_no"] = ic.get("cardNo", "")
                result["meter_no"] = ic.get("meterNo", "")
                result["last_record"] = ic.get("lastRecord", 0)
                result["last_record_time"] = ic.get("lastRecordTime", "")
                result["owe_money"] = float(ic.get("oweMoney", 0))
                result["purch_times"] = int(ic.get("purchTimes", 0))
                result["max_gas"] = int(ic.get("maxGas", 0))

            if payment_data and payment_data.get("data"):
                payments = payment_data["data"]
                if isinstance(payments, list):
                    for p in payments:
                        if p.get("isPay") == 1:
                            result["last_payment"] = {
                                "amount": float(p.get("amount", 0)),
                                "date": p.get("timeope", ""),
                                "desc": p.get("paytypedesc", ""),
                            }
                            break

                    for p in payments:
                        if p.get("isBuy") == 1:
                            result["last_purchase"] = {
                                "qty": float(p.get("qty", 0)),
                                "amount": float(p.get("amount", 0)),
                                "date": p.get("timeope", ""),
                                "desc": p.get("paytypedesc", ""),
                            }
                            break

                    total_purchased = 0
                    total_spent = 0
                    monthly_usage = {}
                    monthly_cost = {}
                    for p in payments:
                        if p.get("isBuy") == 1:
                            qty = float(p.get("qty", 0))
                            amount = float(p.get("amount", 0))
                            total_purchased += qty
                            total_spent += amount
                            month = p.get("recordmonth", "")
                            if month and len(str(month)) == 6:
                                month_key = f"{str(month)[:4]}-{str(month)[4:6]}"
                                monthly_usage[month_key] = monthly_usage.get(month_key, 0) + qty
                                monthly_cost[month_key] = monthly_cost.get(month_key, 0) + amount

                    result["total_purchased"] = total_purchased
                    result["total_spent"] = total_spent
                    result["monthly_usage"] = monthly_usage
                    result["monthly_cost"] = monthly_cost

            # 计算 f_gas_total / e_gas_total (按月汇总)
            f_gas_total = {}
            e_gas_total = {}
            for month_key, qty in result["monthly_usage"].items():
                f_gas_total[month_key] = qty
                e_gas_total[month_key] = result["monthly_cost"].get(month_key, 0)
            result["f_gas_total"] = f_gas_total
            result["e_gas_total"] = e_gas_total

            # 计算每月日均用气 & 生成 daylist（用于弹窗图表）
            daily_avg = {}
            daylist = []
            for month_key, qty in result["monthly_usage"].items():
                try:
                    year, mon = map(int, month_key.split("-"))
                    days = calendar.monthrange(year, mon)[1]
                    daily_avg[month_key] = {
                        "qty": qty,
                        "days": days,
                        "daily_avg": round(qty / days, 2) if days > 0 else 0,
                    }
                    # 为该月每一天生成 daylist 记录
                    for day in range(1, days + 1):
                        day_str = f"{year}-{mon:02d}-{day:02d}"
                        daylist.append({
                            "day": day_str,
                            "f_gas": round(qty / days, 2) if days > 0 else 0,
                            "e_gas": round(e_gas_total.get(month_key, 0) / days, 2) if days > 0 else 0,
                        })
                except Exception:
                    pass
            result["daily_avg"] = daily_avg
            result["daylist"] = daylist

            # 计算近3/12个月平均日均
            now = datetime.now()
            current_ym = now.strftime("%Y-%m")
            sorted_months = sorted(result["monthly_usage"].items(), key=lambda x: x[0], reverse=True)

            # 近3个月
            recent_3 = sorted_months[:3]
            if recent_3:
                total_3 = sum(v for _, v in recent_3)
                days_3 = sum(calendar.monthrange(*map(int, mk.split("-")))[1] for mk, _ in recent_3)
                result["daily_avg_recent_3m"] = round(total_3 / days_3, 2) if days_3 > 0 else 0

            # 近12个月
            recent_12 = sorted_months[:12]
            if recent_12:
                total_12 = sum(v for _, v in recent_12)
                days_12 = sum(calendar.monthrange(*map(int, mk.split("-")))[1] for mk, _ in recent_12)
                result["daily_avg_recent_12m"] = round(total_12 / days_12, 2) if days_12 > 0 else 0

            # 当年累计 + 本年度日均
            year_start = f"{now.year}-01"
            year_accumulated = sum(v for mk, v in sorted_months if mk >= year_start)
            result["year_accumulated"] = year_accumulated
            days_passed = now.day
            result["daily_avg_year"] = round(year_accumulated / days_passed, 2) if days_passed > 0 else 0

            result["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return result

        except Exception as ex:
            _LOGGER.error("处理燃气数据失败: %s", ex)
            return {}


class ChinaGasSensor(SensorEntity):
    """Representation of a China Gas sensor (single entity with all data in attributes)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ChinaGasCoordinator, config: dict):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.config = config
        # 使用 cust_code 作为 entity_id 的一部分
        cust_code = config.get("cust_code", "unknown")
        cust_name = config.get("cust_name", "")
        self._attr_unique_id = f"china_gas_{cust_code}"
        self._attr_name = f"中国燃气 {cust_name} ({cust_code})"
        self._attr_icon = "mdi:fire"
        self._attr_native_unit_of_measurement = "m³"

    @property
    def device_info(self):
        """Return device info for the device registry."""
        cust_code = self.config.get("cust_code", "unknown")
        return {
            "identifiers": {(DOMAIN, cust_code)},
            "name": f"中国燃气 {self.config.get('cust_name', '')}",
            "manufacturer": "中国燃气",
            "model": "燃气在线查询",
            "configuration_url": "https://zrds.95007.com",
        }

    @property
    def available(self):
        return self.coordinator.data is not None

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("balance", 0)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {}

        # 阶梯计费配置
        ladder_level_1 = self.config.get(CONF_LADDER_LEVEL_1, DEFAULT_LADDER_LEVEL_1)
        ladder_level_2 = self.config.get(CONF_LADDER_LEVEL_2, DEFAULT_LADDER_LEVEL_2)
        price_1 = self.config.get(CONF_LADDER_PRICE_1, DEFAULT_LADDER_PRICE_1)
        price_2 = self.config.get(CONF_LADDER_PRICE_2, DEFAULT_LADDER_PRICE_2)
        price_3 = self.config.get(CONF_LADDER_PRICE_3, DEFAULT_LADDER_PRICE_3)
        year_ladder_start = self.config.get(CONF_YEAR_LADDER_START, DEFAULT_YEAR_LADDER_START)

        # 当前阶梯日期范围
        current_date = datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        current_day_int = current_date.day
        start_month = int(year_ladder_start[:2])
        start_day = int(year_ladder_start[2:])

        if (current_month < start_month) or (current_month == start_month and current_day_int < start_day):
            ladder_year = current_year - 1
        else:
            ladder_year = current_year

        year_ladder_start_date = f"{ladder_year}-{year_ladder_start[:2]}-{year_ladder_start[2:]}"
        start_date_next_year = datetime(ladder_year + 1, start_month, start_day)
        end_date = start_date_next_year - timedelta(days=1)
        year_ladder_end_date = f"{end_date.year}-{end_date.month:02d}-{end_date.day:02d}"

        # 计算当前阶梯档位（基于当年累计用气量）
        monthly_usage = self.coordinator.data.get("monthly_usage", {}) if self.coordinator.data else {}
        year_start = f"{current_year}-01"
        year_accumulated = sum(v for mk, v in monthly_usage.items() if mk >= year_start) if monthly_usage else 0

        if year_accumulated <= ladder_level_1:
            current_level = "第1档"
        elif year_accumulated <= ladder_level_2:
            current_level = "第2档"
        else:
            current_level = "第3档"

        # 计费标准 - 直接挂到 attributes 下，模板从此处读取
        billing_attrs = {
            "计费标准": "年阶梯",
            "当前年阶梯档": current_level,
            "年阶梯第2档起始气量": ladder_level_1,
            "年阶梯第3档起始气量": ladder_level_2,
            "年阶梯累计用气量": year_accumulated,
            "当前年阶梯起始日期": year_ladder_start_date,
            "当前年阶梯结束日期": year_ladder_end_date,
            "年阶梯第1档气价": price_1,
            "年阶梯第2档气价": price_2,
            "年阶梯第3档气价": price_3,
        }
        attrs["计费标准"] = billing_attrs

        # 传感器数据
        if self.coordinator.data:
            attrs.update({
                "账户名称": self.coordinator.data.get("cust_name", ""),
                "地址": self.coordinator.data.get("address", ""),
                "IC卡号": self.coordinator.data.get("card_no", ""),
                "燃气表号": self.coordinator.data.get("meter_no", ""),
                "最后抄表读数": self.coordinator.data.get("last_record", 0),
                "最后抄表时间": self.coordinator.data.get("last_record_time", ""),
                "欠费金额": self.coordinator.data.get("owe_money", 0),
                "购气次数": self.coordinator.data.get("purch_times", 0),
                "单次最大购气量": self.coordinator.data.get("max_gas", 0),
                "累计购气量": self.coordinator.data.get("total_purchased", 0),
                "本月用气量": self._get_monthly_usage(),
                "最后同步日期": self.coordinator.last_update_time.strftime("%Y-%m-%d %H:%M:%S"),
            })

            # 最近缴费
            last_payment = self.coordinator.data.get("last_payment")
            if last_payment:
                attrs["最近缴费金额"] = last_payment.get("amount", 0)
                attrs["最近缴费日期"] = last_payment.get("date", "")
                attrs["最近缴费方式"] = last_payment.get("desc", "")
            else:
                attrs["最近缴费金额"] = None
                attrs["最近缴费日期"] = None
                attrs["最近缴费方式"] = None

            # 最近购气
            last_purchase = self.coordinator.data.get("last_purchase")
            if last_purchase:
                attrs["最近购气量"] = last_purchase.get("qty", 0)
                attrs["最近购气金额"] = last_purchase.get("amount", 0)
                attrs["最近购气日期"] = last_purchase.get("date", "")
                attrs["最近购气方式"] = last_purchase.get("desc", "")
            else:
                attrs["最近购气量"] = None
                attrs["最近购气金额"] = None
                attrs["最近购气日期"] = None
                attrs["最近购气方式"] = None

            # 月用气/月费用记录
            monthly_usage = self.coordinator.data.get("monthly_usage", {})
            monthly_cost = self.coordinator.data.get("monthly_cost", {})
            if monthly_usage:
                sorted_months_usage = sorted(monthly_usage.items(), key=lambda x: x[0], reverse=True)[:12]
                attrs["月用气记录"] = {k: v for k, v in sorted_months_usage}
                sorted_months_cost = sorted(monthly_cost.items(), key=lambda x: x[0], reverse=True)[:12]
                attrs["月费用记录"] = {k: v for k, v in sorted_months_cost}
            else:
                attrs["月用气记录"] = {}
                attrs["月费用记录"] = {}

            # f_gas_total / e_gas_total (按月汇总，用于弹窗)
            f_gas_total = self.coordinator.data.get("f_gas_total", {})
            e_gas_total = self.coordinator.data.get("e_gas_total", {})
            if f_gas_total:
                attrs["f_gas_total"] = f_gas_total
                attrs["e_gas_total"] = e_gas_total

            # monthlist (用于弹窗阶梯/本月/上月/本年统计)
            # 包含当前月和上月（即使无数据也要补齐，模板find()需匹配到）
            monthlist = []
            if f_gas_total:
                monthlist = [
                    {"month": m, "f_gas_total": q, "e_gas_total": e_gas_total.get(m, 0)}
                    for m, q in sorted(f_gas_total.items(), reverse=True)
                ]
            existing_months = {item["month"] for item in monthlist}
            cur_month = f"{current_year}-{str(current_month).zfill(2)}"
            prev_month = f"{current_year}-{str(current_month - 1).zfill(2)}" if current_month > 1 else f"{current_year - 1}-12"
            for m in [cur_month, prev_month]:
                if m not in existing_months:
                    monthlist.append({"month": m, "f_gas_total": 0, "e_gas_total": 0})
            if monthlist:
                attrs["monthlist"] = sorted(monthlist, key=lambda x: x["month"], reverse=True)

            # yearlist (用于模板计算年度数据)
            if f_gas_total:
                year_map = {}
                for m, q in f_gas_total.items():
                    year = m[:4]
                    if year not in year_map:
                        year_map[year] = {"year": year, "yearEleNum": 0, "yearEleCost": 0}
                    year_map[year]["yearEleNum"] += q
                    year_map[year]["yearEleCost"] += e_gas_total.get(m, 0)
                attrs["yearlist"] = sorted(year_map.values(), key=lambda x: x["year"], reverse=True)

            # daylist (用于弹窗图表)
            if self.coordinator.data.get("daylist"):
                attrs["daylist"] = self.coordinator.data.get("daylist", [])

            # 日均用气
            daily_avg = self.coordinator.data.get("daily_avg", {})
            if daily_avg:
                attrs["日均用气"] = {k: v["daily_avg"] for k, v in sorted(daily_avg.items(), reverse=True)}
            else:
                attrs["日均用气"] = {}

            attrs["近3个月日均"] = self.coordinator.data.get("daily_avg_recent_3m", 0)
            attrs["近12个月日均"] = self.coordinator.data.get("daily_avg_recent_12m", 0)
            attrs["本年度日均"] = self.coordinator.data.get("daily_avg_year", 0)
            attrs["本年度累计购气量"] = self.coordinator.data.get("year_accumulated", 0)
            attrs["总购气金额"] = self.coordinator.data.get("total_spent", 0)

            # 卡片模板兼容：balance 和 usagedays
            balance = self.coordinator.data.get("balance", 0)
            attrs["余额"] = balance
            attrs["balance"] = balance
            # 预计可用天数 = 余额 / 近3个月日均用气
            daily_avg_3m = self.coordinator.data.get("daily_avg_recent_3m", 0)
            if daily_avg_3m > 0 and balance > 0:
                usagedays = round(balance / daily_avg_3m)
            else:
                usagedays = 0
            attrs["usagedays"] = usagedays
            # 价格和金额信息
            attrs["预付费"] = "是" if self.config.get(CONF_IS_PREPAID, False) else "否"
        else:
            attrs["账户名称"] = None
            attrs["地址"] = None
            attrs["IC卡号"] = None
            attrs["燃气表号"] = None
            attrs["最后抄表读数"] = None
            attrs["最后抄表时间"] = None
            attrs["欠费金额"] = None
            attrs["购气次数"] = None
            attrs["单次最大购气量"] = None
            attrs["累计购气量"] = None
            attrs["本月用气量"] = None
            attrs["最近缴费金额"] = None
            attrs["最近缴费日期"] = None
            attrs["最近缴费方式"] = None
            attrs["最近购气量"] = None
            attrs["最近购气金额"] = None
            attrs["最近购气日期"] = None
            attrs["最近购气方式"] = None
            attrs["日均用气"] = {}
            attrs["近3个月日均"] = None
            attrs["近12个月日均"] = None
            attrs["本年度日均"] = None
            attrs["本年度累计购气量"] = None
            attrs["总购气金额"] = None
            attrs["月用气记录"] = {}
            attrs["月费用记录"] = {}
            attrs["f_gas_total"] = {}
            attrs["e_gas_total"] = {}
            attrs["monthlist"] = []
            attrs["yearlist"] = []
            attrs["daylist"] = []
            attrs["余额"] = 0
            attrs["usagedays"] = 0
            attrs["最后同步日期"] = None
            attrs["预付费"] = "否"

        attrs["数据源"] = "中国燃气"

        return attrs

    def _get_monthly_usage(self):
        """获取本月用气量"""
        if not self.coordinator.data:
            return 0
        current_month = datetime.now().strftime("%Y-%m")
        monthly_usage = self.coordinator.data.get("monthly_usage", {})
        return monthly_usage.get(current_month, 0)

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
