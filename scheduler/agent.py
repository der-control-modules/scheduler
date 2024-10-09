# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright 2024, Battelle Memorial Institute.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# This material was prepared as an account of work sponsored by an agency of
# the United States Government. Neither the United States Government nor the
# United States Department of Energy, nor Battelle, nor any of their
# employees, nor any jurisdiction or organization that has cooperated in the
# development of these materials, makes any warranty, express or
# implied, or assumes any legal liability or responsibility for the accuracy,
# completeness, or usefulness or any information, apparatus, product,
# software, or process disclosed, or represents that its use would not infringe
# privately owned rights. Reference herein to any specific commercial product,
# process, or service by trade name, trademark, manufacturer, or otherwise
# does not necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors expressed
# herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY operated by
# BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830
# }}}


import logging
import pandas as pd
import sys
import os
import gevent
import json
from datetime import datetime, timedelta, timezone
from pandas.tseries.holiday import USFederalHolidayCalendar as hl_day
from control.optimization import Optimization
from volttron.platform.agent import utils
from volttron.platform.agent.utils import format_timestamp, get_aware_utc_now, parse_timestamp_string
from volttron.platform.messaging import topics
from volttron.platform.messaging.health import STATUS_GOOD
from volttron.platform.vip.agent import Agent, Core, PubSub, RPC
from volttron.platform.scheduling import cron
from volttron.platform.jsonrpc import RemoteError
from volttron.platform.vip.agent.subsystems.query import Query
from dateutil import parser
import dateutil
import math
import time

utils.setup_logging()
_log = logging.getLogger(__name__)
__version__ = '1.0'


class Optimize(Agent):
    """Optimize BESS operations daily based on hourly price and load forecast.
    """

    def __init__(self, config_path, **kwargs):
        super(Optimize, self).__init__(**kwargs)
        # If a configuration file is used the agent will utilize
        # this for the default configuration.  If a config is in
        # config store this will override the config file.
        file_config = utils.load_config(config_path)
        default_config = {
            "campus": "PNNL",
            "actuator": "platform.actuator",
            "building": "SEB",
            "device": "BESS_OPT",
            "prerequisites": {},
            "run_schedule": "0 0 * * *"
        }
        if file_config:
            self.default_config = file_config
        else:
            self.default_config = default_config
        try:
            self.localtz = dateutil.tz.tzlocal()
        except:
            _log.warning(
                "Problem automatically determining timezone! - Default to UTC.")
            self.localtz = "UTC"

        self.config = self.default_config.copy()
        self.run_schedule = None
        self.bess_actuator = None
        self.tess_actuator = None
        self.price_file = None
        self.load_file = None
        self.bess_optimizer = None
        self.oat_point_name = "temperature"
        self.peak_load_prediction = None
        self.season = "Summer"
        self.weather_vip = 'platform.weather'
        #self.location = [{"wfo": "PDT", "x": 119, "y": 131}]
        self.demand_rate_config = []
        self.price = []
        self.load = []
        self.uncontrollable_load = []
        self.soc_prediction = []
        self.setpoints = []
        self.total_power = []
        self.bess_soc = None
        self.tess_soc = None
        self.max_soc = 90
        self.min_soc = 10
        # self.bess_optimizer_config = {}
        # self.tess_optimizer_config = {}
        self.schedule_objects = []
        self.ess_results = {}
        self.bess_soc_topic = ""
        self.publish_topic = ""
        self.window_length = 24
        self.data_source = "postgres.cetc"
        self.external_platform = "vc"
        # self.load_topic =  "PNNL/SEB/ELECTRIC_METER/WholeBuildingPower"
        self.forecast_data_source = "info_agent"
        self.price_topic = "devices/PNNL/grid_information/price/all"
        self.price_point = "tou"
        self.load_forecast_topic = "devices/PNNL/SEB/forecast/all"
        self.load_forecast_point = "load"
        self.tess_topic = 'devices/PNNL/SEB/TSS/SUPERVISORY_CONTROLLER/all'
        self.uncontrollable_load_forecast_point = "uncontrollable_load"
        self.energy_storage_system = "bess"
        self.start_day_data_collection = 1
        self.duration_data_collection = 12
        self.rounding_precision = 2
        self.tess_direct_signal = 10
        self.hours_to_start = 2
        self.cooling_load = []
        self.cop = 5
        self.identity = "tess.control"
        self.method = "control"
        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(self.configure_main,
                                  actions=["NEW", "UPDATE"],
                                  pattern="config")
        self._last_soc_time = datetime(
            1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.soc_stale = timedelta(seconds=150)
        # self.pub_lock = False

    def configure_main(self, config_name, action, contents):
        """This triggers configuration of the BESS Optimize Agent via
        the VOLTTRON configuration store.
        :param config_name: canonical name is config
        :param action: on instantiation this is "NEW" or
        "UPDATE" if user uploads update config to store
        :param contents: configuration contents
        :return: None
        """
        _log.debug("Update %s for %s", config_name, self.core.identity)
        self.config = self.default_config.copy()
        self.config.update(contents)
        campus = self.config.get("campus", "")
        building = self.config.get("building", "")
        device = self.config.get("device", "")
        self.energy_storage_system = self.config.get(
            "energy_storage_system", self.energy_storage_system).lower()
        soc_point = self.config.get("soc_point_name", "BAT_SOC")
        self.season = self.config.get("season", self.season)
        self.publish_topic = "record/{}/{}/{}/{}".format(
            campus, building, device, "schedule")
        # record/PNNL/SEB/BESS/schedule
        # record/PNNL/SEB/TESS/schedule
        self.bess_soc_topic = topics.RPC_DEVICE_PATH(campus=campus,
                                                     building=building,
                                                     unit="BESS",
                                                     path="",
                                                     point=soc_point)
        self.weather_vip = self.config.get("weather_vip", self.weather_vip)
        self.data_source = self.config.get("data_source", self.data_source)
        self.external_platform = self.config.get(
            "external_platform", self.external_platform)
        self.tess_topic = self.config.get("tess_topic", self.tess_topic)
        self.soc_stale = self.config.get("soc_stale_timedelta", self.soc_stale)
        chiller_config = self.config['chiller_config']
        self.cop = chiller_config.get('COP', 3.5)
        self.hours_to_start = self.config.get(
            "hours_to_start", self.hours_to_start)

        forecast_config = self.config.get("forecast_config")
        self.forecast_data_source = forecast_config.get(
            "forecast_data_source", self.forecast_data_source)
        self.price_topic = forecast_config.get("price_topic", self.price_topic)
        self.load_forecast_topic = forecast_config.get(
            "load_forecast_topic", self.load_forecast_topic)
        self.load_forecast_point = self.config.get(
            "load_forecast_point", self.load_forecast_point)
        self.uncontrollable_load_forecast_point = forecast_config.get("uncontrollable_load_forecast_point",
                                                                      self.uncontrollable_load_forecast_point)

        _log.debug(f"Energy storage system is {self.energy_storage_system}")
        self.tess_direct_signal = self.config.get(
            "tess_direct_signal", self.tess_direct_signal)
        self.identity = self.config.get("identity", "tess.schedule")
        self.method = self.config.get("method", "control")
        _log.debug(f"Method is {self.method}")

        if action == "NEW" or "UPDATE":
            if self.forecast_data_source == "info_agent":
                self.vip.pubsub.subscribe(peer='pubsub',
                                          prefix=self.price_topic,
                                          callback=self.on_grid_signal)

                self.vip.pubsub.subscribe(peer='pubsub',
                                          prefix=self.load_forecast_topic,
                                          callback=self.on_load_forecast)
                self.vip.pubsub.subscribe(peer='pubsub',
                                          prefix=self.tess_topic,
                                          callback=self.on_tess_data, all_platforms=True)
                self.vip.pubsub.subscribe(peer='pubsub',
                                          prefix=self.bess_topic,
                                          callback=self.on_bess_data, all_platforms=True)
            else:
                self.price = forecast_config.get("predicted_price")
                self.load = forecast_config.get("predicted_load")
                self.uncontrollable_load = forecast_config.get(
                    "predicted_uncontrollable_load")
            self.run_schedule = self.config.get("run_schedule")
            _log.debug("Run schedule: {}".format(self.run_schedule))
            self.bess_actuator = self.config.get(
                "bess_actuator_vip", "bess.control")
            self.tess_actuator = self.config.get(
                "tess_actuator_vip", "tess.control")
            self.setpoints = (self.config.get("bess_setpoints", [])
                              if self.energy_storage_system == "bess"
                              else self.config.get("tess_setpoints", [])
                              if self.energy_storage_system == "tess"
                              else [])
            _log.debug(f"Energy storage setpoints are {self.setpoints}")
            self.load_file = self.config.get(
                "load_file", "optimize/data/SEB_power_profile.csv")
            self.window_length = self.config.get("window_length", 24)
            # self.bess_optimizer_config = self.config.get('bess_optimizer_config', {})
            # self.tess_optimizer_config = self.config.get('tess_optimizer_config', {})
            # self.chiller_config = self.config.get('chiller_config', {})
            # self.demand_rate_config = self.config.get('demand_rate_config', {})

            gevent.spawn_later(5, self.starting_base)

    def starting_base(self, **kwargs):
        """Instantiate optimizer
        :param: kwargs: empty
        :return: None
        """
        self.get_soc()
        if 'hybrid' in self.energy_storage_system:
            _log.debug(f"current SOC for hybrid system: TESS_SOC = {self.tess_soc}, BESS_SOC = {self.bess_soc}")
        elif 'tess' in self.energy_storage_system:
            _log.debug(f"current SOC for TESS is {self.tess_soc}")
        elif 'bess' in self.energy_storage_system:
            _log.debug(f"current SOC for BESS is {self.bess_soc}")
        else:
            _log.debug(f"not correct configuration for {self.energy_storage_system}")

        if self.method.lower() == "control":
            if self.forecast_data_source == "info_agent":
                next_run = datetime.now().replace(minute=0, second=0, microsecond=0) + \
                    timedelta(hours=self.hours_to_start, minutes=5)
            else:
                next_run = datetime.now().replace(
                    minute=0, second=0, microsecond=0) + timedelta(minutes=1)
            # next_run = datetime.now().replace(minute=0, second=0, microsecond=0)
            if self.hours_to_start:
                self.core.schedule(next_run, self.run_process)
            self.core.schedule(cron(self.run_schedule), self.run_process)
        elif self.method.lower() == "schedule":
            next_run = datetime.now().replace(
                minute=0, second=0, microsecond=0) + timedelta(minutes=1)

            # next_run = datetime.now().replace(minute=0, second=0, microsecond=0)
            self.core.schedule(next_run, self.run_process)
        elif self.method.lower() == "direct":
            next_run = datetime.now() + timedelta(minutes=2)
            self.core.schedule(next_run, self.run_process)
        else:
            pass

    def process_dict_message(self, message, key_point, storage_list, topic, description):
        """
        Process a dictionary message to extract the value for the given key_point and store it in the storage_list.
        Prints the received value or an error message if the key_point is not found.

        Args:
            message (dict): The message containing the data.
            key_point (str): The key to look for in the message.
            storage_list (list): The list to store the extracted value.
            topic (str): The topic from which the message was received.
            description (str): Description of the value being processed (e.g., 'price', 'load').
        """
        if key_point in message:
            value = message[key_point]
            storage_list.append(value)
            # print(f'Received {description}: {value} on topic: {topic}')
        else:
            _log.debug(
                f"Not received {description} on topic {topic} with message = {message}")

    def process_message(self, message, key_point, storage_list, topic, description):
        """
        Process the message to extract the value for the given key_point and store it in the storage_list.
        Prints the received value or an error message if the key_point is not found.

        Args:
            message (dict or list): The message containing the data.
            key_point (str): The key to look for in the message.
            storage_list (list): The list to store the extracted value.
            topic (str): The topic from which the message was received.
            description (str): Description of the value being processed (e.g., 'price', 'load').
        """
        if isinstance(message, dict):
            # If the message is a dictionary, extract the value for the given key_point
            self.process_dict_message(
                message, key_point, storage_list, topic, description)
        else:
            # If the message is a list, extract the value from the first element
            message = message[0]
            self.process_dict_message(
                message, key_point, storage_list, topic, description)

    def on_tess_data(self, peer, sender, bus, topic, headers, message):
        if isinstance(message, dict):
            message = message
            # If the message is a dictionary, extract the value for the given key_point
        else:
            # If the message is a list, extract the value from the first element
            message = message[0]
        key_point = 'IceTankPercentCharge'
        if key_point in message:
            received_datetime = parse_timestamp_string(headers['TimeStamp'])
            if received_datetime > self._last_soc_time:
                self.tess_soc = message[key_point]
                _log.debug(
                    f'Received from pubsub soc : {self.tess_soc} on topic: {topic}')
                self._last_soc_time = received_datetime

        else:
            print(
                f"Not received soc on topic {topic} with message = {message}")

    def on_grid_signal(self, peer, sender, bus, topic, headers, message):
        """
        Handle the grid signal message to extract and store the price.

        Args that matters:
            topic (str): The topic of the message.
            message (dict or list): The message containing the data.
        """
        self.process_message(message, self.price_point,
                             self.price, topic, 'price')

    def on_load_forecast(self, peer, sender, bus, topic, headers, message):
        """
        Handle the load forecast message to extract and store the load and uncontrollable load forecast.

        Args that matters:
            topic (str): The topic of the message.
            message (dict or list): The message containing the data.
        """
        self.process_message(
            message, self.load_forecast_point, self.load, topic, 'load')
        self.process_message(message, self.uncontrollable_load_forecast_point,
                             self.uncontrollable_load, topic, 'uncontrollable load')

    def update_schedule(self, setpoints):
        _hour = datetime.now().hour
        ld = []
        for ind in range(_hour, 24):
            ld.append(setpoints[ind])
        for ind in range(0, _hour):
            ld.append(setpoints[ind])
        return ld

    def schedule_operations(self):
        headers = {'Date': format_timestamp(get_aware_utc_now())}
        self.get_schedule_from_control()
        message_dict = self.ess_results
        
        for i in range(self.window_length):
            sched_hour = datetime.now() + timedelta(hours=i)
            run_time = sched_hour.replace(minute=0, second=0, microsecond=0)
            
            # Schedule actions for TESS or BESS, or hybrid
            if self.energy_storage_system == "tess":
                setpoints = round(self.ess_results['tess_power'][i], self.rounding_precision)
                adjusted_setpoints = setpoints - self.cooling_load[i] * self.cop if setpoints < 0 else setpoints
                _log.debug(f"Updated {self.energy_storage_system} setpoints are {adjusted_setpoints}")
                self.schedule_objects.append(self.core.schedule(
                    run_time, self.actuate_storage, adjusted_setpoints))
            elif self.energy_storage_system == "bess":
                setpoints = round(self.ess_results['bess_power'][i], self.rounding_precision)
                _log.debug(f"Updated {self.energy_storage_system} setpoints are {setpoints}")
                self.schedule_objects.append(self.core.schedule(
                    run_time, self.actuate_storage, setpoints))
            elif self.energy_storage_system == "hybrid":
                tess_setpoints = round(self.ess_results['tess_power'][i], self.rounding_precision)
                bess_setpoints = round(self.ess_results['bess_power'][i], self.rounding_precision)
                _log.debug(f"Updated {self.energy_storage_system} setpoints: tess = {tess_setpoints}")
                _log.debug(f"Updated {self.energy_storage_system} setpoints: bess = {bess_setpoints}")
                self.schedule_objects.append(self.core.schedule(
                    run_time, self.actuate_storage, (tess_setpoints, bess_setpoints)))
            else:
                # Default case for other systems
                pass

            forecast_time = (get_aware_utc_now() + timedelta(hours=i)
                             ).replace(minute=0, second=0, microsecond=0)

            if self.method.lower() == "control":
                # algorithm
                message_dict[forecast_time] = {
                        "duration_in_second": 3600
                    }
            elif self.method.lower() == "schedule":
                # configure
                message_dict[forecast_time] ={
                        f"{self.energy_storage_system}_setpoints": float(setpoints)
                    }
            else:
                pass
            
        self.publish_data(headers, message_dict)

    def forward_fill_na(self, lst):
        if not lst:
            return lst

        latest_value = None
        for i in range(len(lst)):
            if math.isnan(lst[i]) and latest_value is not None:
                lst[i] = latest_value
            elif not math.isnan(lst[i]):
                latest_value = lst[i]

        return lst

    def backward_fill_na(self, lst):
        if not lst:
            return lst

        latest_value = None
        # Loop backwards over the list
        for i in range(len(lst) - 1, -1, -1):
            if math.isnan(lst[i]) and latest_value is not None:
                lst[i] = latest_value
            elif not math.isnan(lst[i]):
                latest_value = lst[i]

        return lst

    def clear_schedule(self):
        for sched in self.schedule_objects:
            sched.cancel()
        self.schedule_objects = []
        if self.energy_storage_system == 'bess':
            self.get_soc()

    def get_schedule_from_control(self):
        # if self.energy_storage_system == "bess":
        self.optimizer = Optimization(self.load, self.uncontrollable_load, self.price, self.config)
        self.clear_schedule()
        if self.forecast_data_source == "info_agent":
            self.optimizer.update(bess_soc=self.bess_soc, tess_soc=self.tess_soc)
        else:
            self.optimizer.update(self.load, bess_soc=self.bess_soc, tess_soc=self.tess_soc)
        self.ess_results = self.optimizer.run_opt()

    def run_process(self):
        self.load = self.backward_fill_na(self.forward_fill_na(self.load))
        self.price = self.forward_fill_na(self.price)

        if self.method.lower() == "control":
            self.schedule_operations()

        elif self.method.lower() == "schedule":
            self.setpoints = self.update_schedule(self.setpoints)
            self.schedule_operations()
        elif self.method.lower() == "direct":
            self.actuate_storage(self.tess_direct_signal)
        else:
            pass

    def publish_data(self, headers, message):
        # publish given message in the volttron's message bus
        try:
            self.vip.pubsub.publish('pubsub', self.publish_topic,
                                    headers=headers, message=message).get(timeout=25)

        except Exception as err:
            _log.error("In Publish: {}".format(str(err)))
            
def actuate_storage(self, value):
    """
    Actuate storage for BESS (Battery Energy Storage System), TESS, or both based on the provided value.
    The value can be a float or a tuple, where the tuple contains (tess_setpoint, bess_setpoint).

    :param value: Control value for BESS or TESS actuation. It can be a float or a tuple.
    """

    for attempt in range(10):
        try:
            # Initialize setpoints for TESS and BESS
            tess_setpoint, bess_setpoint = None, None

            # Handle value based on energy storage system type
            if self.energy_storage_system == "hybrid" and isinstance(value, tuple):
                tess_setpoint, bess_setpoint = value  # Value is a tuple with both TESS and BESS setpoints
            elif self.energy_storage_system == "bess" and isinstance(value, (int, float)):
                bess_setpoint = value  # Value is for BESS only
            elif self.energy_storage_system == "tess" and isinstance(value, (int, float)):
                tess_setpoint = value  # Value is for TESS only
            else:
                _log.error("Invalid value type or energy storage system type.")
                return  # Exit if value type is invalid for the given system

            # Handle charging (negative setpoints)
            if tess_setpoint is not None and tess_setpoint < 0:
                if self.energy_storage_system in ["tess", "hybrid"]:
                    if self.allowed_by_soc(tess_setpoint):
                        tess_setpoint /= self.cop  # Adjust TESS cooling value
                        t_run_seconds = abs(tess_setpoint) / 40 * 3600 + 200
                        
                        self._call_tess_actuator("charge")

                        if t_run_seconds < 3000:
                            t_run_seconds = max(t_run_seconds, 800)
                            _log.debug(f"Adjusted TESS run time = {t_run_seconds} seconds")

                            run_time = datetime.now() + timedelta(seconds=t_run_seconds)
                            _log.debug(f"Scheduled TESS cooling at {run_time}")
                            
                            self.core.schedule(run_time, self._call_tess_actuator, "cooling")
                    else:
                        self._call_tess_actuator("cooling")

            if bess_setpoint is not None and bess_setpoint < 0:
                if self.energy_storage_system in ["bess", "hybrid"]:
                    self._call_bess_actuator(bess_setpoint, "charge")

            # Handle discharging (positive setpoints)
            if tess_setpoint is not None and tess_setpoint > 0:
                if self.energy_storage_system in ["tess", "hybrid"]:
                    if self.allowed_by_soc(tess_setpoint):
                        self._call_tess_actuator("discharge")
                    else:
                        self._call_tess_actuator("cooling")

            if bess_setpoint is not None and bess_setpoint > 0:
                if self.energy_storage_system in ["bess", "hybrid"]:
                    self._call_bess_actuator(bess_setpoint, "discharge")

            # Handle zero value (turn off or cooling)
            if tess_setpoint == 0:
                if self.energy_storage_system in ["tess", "hybrid"]:
                    self._call_tess_actuator("cooling")

            if bess_setpoint == 0:
                if self.energy_storage_system in ["bess", "hybrid"]:
                    self._call_bess_actuator(0, "off")

        except (gevent.Timeout, RemoteError) as e:
            _log.debug(f"Trial {attempt} failed: Error actuating {self.energy_storage_system} - {e}")
            continue

        break  # Exit the loop if no exception occurred

    def _call_bess_actuator(self, value, call):
        if not 'bess.rtc' in self.vip.peerlist().get():
            self.vip.rpc.call(self.bess_actuator, call,
                              abs(value)).get(timeout=10)
            if call == 'off':
                self.vip.rpc.call(self.bess_actuator, call).get(timeout=10)

    def _call_tess_actuator(self, call):
        operation = 0
        self.vip.rpc.call("platform.rpc_relay",
                          "relay",
                          "tess",
                          self.tess_actuator,
                          call,
                          external_platform=self.external_platform).get(timeout=10)

        if call == "charge":
            operation = 3
        elif call == "discharge":
            operation = 2
        elif call == "cooling":
            operation = 1
        else:
            operation = 0

        headers = {'Date': format_timestamp(get_aware_utc_now())}
        message = [
            {
                f"{self.energy_storage_system}_operation": operation,
            },
            {
                f"{self.energy_storage_system}_operation": {"units": "na", "tz": "PST", "type": "str"},
            }
        ]
        self.publish_data(headers, message)

    def allowed_by_soc(self, value):
        if get_aware_utc_now() - self._last_soc_time > self.soc_stale:
            _log.warning(
                f'Declining to actuate TESS as SOC is stale; last received {self._last_soc_time}')
            return False
        if value < 0 and self.tess_soc >= self.max_soc - 1:
            _log.warning(
                f'Declining to actuate TESS as SOC beyond limit; soc is {self.tess_soc}')
            return False
        if value > 0 and self.tess_soc <= self.min_soc + 1:
            _log.warning(
                f'Declining to actuate TESS as SOC beyond limit; soc is {self.tess_soc}')
            return False
        return True

    def get_soc(self):
        for _ in range(10):
            try:
                if self.energy_storage_system == "bess":
                    self.bess_soc = self.vip.rpc.call(
                        "platform.actuator", "get_point", self.bess_soc_topic).get(timeout=30)
                elif self.energy_storage_system == "tess":
                    self.vip.rpc.call("platform.rpc_relay", "register_subscription",
                                      {'topic': self.tess_topic, 'identity': self.core.identity,
                                       'platform': 'bess', 'function': 'update_tess_data'},
                                      external_platform=self.external_platform).get(timeout=10)

                break
            except (gevent.Timeout, RemoteError, Exception) as e:
                _log.debug(f"number of trials {_} error actuating {e}")
                continue
            # break

    @RPC.export
    def update_tess_data(self, data):
        # _log.debug(f"Getting tess data = {data}")
        data = data[0]
        if 'IceTankPercentCharge' in data:
            self.tess_soc = data['IceTankPercentCharge']
            _log.debug(f"Getting tess SOC data = {self.tess_soc}")
            self._last_soc_time = get_aware_utc_now()
        else:
            _log.debug(
                f"real-time SOC data is not available, using default value")


def main(argv=sys.argv):
    '''Main method called by the eggsecutable.'''
    try:
        utils.vip_main(Optimize, version=__version__)
    except Exception as e:
        _log.exception('unhandled exception')


if __name__ == '__main__':
    # Entry point for script
    sys.exit(main())
