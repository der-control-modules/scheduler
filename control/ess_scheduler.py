import pandas as pd
import math
from datetime import datetime, timedelta
import json
import matplotlib.pyplot as plt
import os
from optimization import Optimization

# Global constants
ROUNDING_PRECISION_DEFAULT = 2
COP_DEFAULT = 3.5
SOC_DEFAULT = {
    "tess": 20,
    "bess": 50
}

# Configuration loading class
class ConfigLoader:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self):
        with open(self.config_path) as json_data_file:
            return json.load(json_data_file)

    def get(self, key, default=None):
        return self.config.get(key, default)


# Scheduler class to handle schedule operations
class Scheduler:
    def __init__(self, config):
        self.config = config
        self.energy_storage_system = config.get("energy_storage_system", "hybrid").lower()
        self.season = config.get("season", "summer")
        self.hours_to_start = config.get("hours_to_start", 1)
        self.rounding_precision = config.get("rounding_precision", ROUNDING_PRECISION_DEFAULT)
        self.method = config.get("method", "control")
        self.window_length = config.get("window_length", 24)
        self.forecast_config = config.get("forecast_config", {})
        self.price = self.forecast_config.get("predicted_price", [])
        self.load = self.forecast_config.get("predicted_load", [])
        self.uncontrollable_load = self.forecast_config.get("predicted_uncontrollable_load", [])
        self.bess_soc = SOC_DEFAULT['bess']
        self.tess_soc = SOC_DEFAULT['tess']
        self.cop = config.get("chiller_config", {}).get("COP", COP_DEFAULT)

    def forward_fill_na(self, lst):
        latest_value = None
        for i in range(len(lst)):
            if math.isnan(lst[i]) and latest_value is not None:
                lst[i] = latest_value
            elif not math.isnan(lst[i]):
                latest_value = lst[i]
        return lst

    def backward_fill_na(self, lst):
        latest_value = None
        for i in range(len(lst) - 1, -1, -1):
            if math.isnan(lst[i]) and latest_value is not None:
                lst[i] = latest_value
            elif not math.isnan(lst[i]):
                latest_value = lst[i]
        return lst

    def update_schedule(self, setpoints):
        _hour = datetime.now().hour
        return setpoints[_hour:] + setpoints[:_hour]

    def get_schedule_from_control(self):
        optimizer = Optimization(self.load, self.uncontrollable_load, self.price, self.config)
        if self.forecast_config.get("data_source") == "info_agent":
            optimizer.update(bess_soc=self.bess_soc, tess_soc=self.tess_soc)
        else:
            optimizer.update(self.load, bess_soc=self.bess_soc, tess_soc=self.tess_soc)
        return optimizer.run_opt()

    def schedule_operations(self):
        message_dict = {}
        for i in range(self.window_length):
            sched_hour = datetime.now() + timedelta(hours=i)
            run_time = sched_hour.replace(minute=0, second=0, microsecond=0)
            ess_results = self.get_schedule_from_control()
            cooling_load = ess_results.get('cooling_load', [])

            if self.energy_storage_system == "tess":
                setpoints = round(ess_results['tess_power'][i], self.rounding_precision)
                setpoints -= cooling_load[i] * self.cop if setpoints < 0 else setpoints
                print(f"{self.energy_storage_system} run time = {run_time} setpoints are {setpoints}")
            elif self.energy_storage_system == "bess":
                setpoints = round(ess_results['bess_power'][i], self.rounding_precision)
                print(f"{self.energy_storage_system} run time = {run_time} setpoints are {setpoints}")
            elif self.energy_storage_system == "hybrid":
                tess_setpoints = round(ess_results['tess_power'][i], self.rounding_precision)
                bess_setpoints = round(ess_results['bess_power'][i], self.rounding_precision)
                print(f"{self.energy_storage_system} run time = {run_time} tess setpoints are {tess_setpoints}, bess setpoints are {bess_setpoints}")
            
            forecast_time = datetime.now().replace(minute=0, second=0, microsecond=0)

            if self.method.lower() == "control":
                message_dict[forecast_time] = {"duration_in_seconds": 3600}
            elif self.method.lower() == "schedule":
                message_dict[forecast_time] = {f"{self.energy_storage_system}_setpoints": float(setpoints)}

        print(message_dict)


# Main process class
class MainProcess:
    def __init__(self, config):
        self.scheduler = Scheduler(config)
        self.setpoints = []

    def run(self):
        # Fill missing values in load and price
        self.scheduler.load = self.scheduler.backward_fill_na(self.scheduler.forward_fill_na(self.scheduler.load))
        self.scheduler.price = self.scheduler.forward_fill_na(self.scheduler.price)

        # Run based on the method specified in the config
        if self.scheduler.method.lower() == "control":
            self.scheduler.get_schedule_from_control()
            self.scheduler.schedule_operations()
        elif self.scheduler.method.lower() == "schedule":
            self.setpoints = self.scheduler.update_schedule(self.setpoints)
            self.scheduler.schedule_operations()
        elif self.scheduler.method.lower() == "direct":
            print("Direct actuation")
        else:
            pass


# Execute the main process
if __name__ == "__main__":
    config_loader = ConfigLoader("der_management/der_agents/scheduler/config")
    main_process = MainProcess(config_loader.config)
    main_process.run()
