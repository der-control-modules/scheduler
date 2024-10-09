
import pandas as pd
import math
from datetime import datetime, timedelta
import json
import matplotlib.pyplot as plt
import os
# import numpy as np
# from model.hot5 import Hot5
# from model.chiller_model import ChillerModel
from control.optimization import Optimization

#os.chdir("../..")

print(os.getcwd())
path = "/home/volttron/optimization_real_system/der_management/der_agents/scheduler"
#path = "der_management/der_agents/scheduler"
wd = datetime.now().weekday()
with open(f"{path}/config_test") as json_data_file:
    config = json.load(json_data_file)

#Global variable    
energy_storage_system = "hybrid"
season = "summer"
hours_to_start = 1
rounding_precision = 2

    
campus = config.get("campus", "")
building = config.get("building", "")
device = config.get("device", "")
energy_storage_system = config.get(
    "energy_storage_system", energy_storage_system).lower()
soc_point = config.get("soc_point_name", "BAT_SOC")
season = config.get("season", season)
publish_topic = "record/{}/{}/{}/{}".format(
    campus, building, device, "schedule")
chiller_config = config['chiller_config']
cop = chiller_config.get('COP', 3.5)
hours_to_start = config.get(
    "hours_to_start", hours_to_start)

forecast_config = config.get("forecast_config")
method = config.get("method", "control")
window_length = config.get("window_length", 24)


energy_storage_system = config.get("energy_storage_system")
forecast_config = config.get("forecast_config")
forecast_data_source = "config"
window_length = config("window_length")
tess_soc = 20
bess_soc = 50
price = forecast_config.get("predicted_price")
load = forecast_config.get("predicted_load")
uncontrollable_load = forecast_config.get("predicted_uncontrollable_load")
cop = config.get("forecast_config")

rounding_precision = config.get("rounding_precision", rounding_precision)
_hour = 0
print(f"price = {price}")
print(f"load = {load}")
print(f"uncontrollable_load = {uncontrollable_load}")


def get_schedule_from_control():
    # if energy_storage_system == "bess":
    optimizer = Optimization(load, uncontrollable_load, price, config)
    if forecast_data_source == "info_agent":
        optimizer.update(bess_soc=bess_soc, tess_soc=tess_soc)
    else:
        optimizer.update(load, bess_soc=bess_soc, tess_soc=tess_soc)
    ess_results = optimizer.run_opt()
    return ess_results

def schedule_operations():
    
    for i in range(window_length):
        sched_hour = datetime.now() + timedelta(hours=i)
        run_time = sched_hour.replace(minute=0, second=0, microsecond=0)
        ess_results = message_dict = get_schedule_from_control()
        cooling_load = ess_results['cooling_load']
        # Schedule actions for TESS or BESS, or hybrid
        if energy_storage_system == "tess":
            setpoints = round(ess_results['tess_power'][i], rounding_precision)
            setpoints = setpoints - cooling_load[i] * cop if setpoints < 0 else setpoints
            print(f"{energy_storage_system} run time = {run_time} setpoints are {setpoints}")
        elif energy_storage_system == "bess":
            setpoints = round(ess_results['bess_power'][i], rounding_precision)
            print(f"{energy_storage_system} run time = {run_time} setpoints are {setpoints}")
        elif energy_storage_system == "hybrid":
            tess_setpoints = round(ess_results['tess_power'][i], rounding_precision)
            bess_setpoints = round(ess_results['bess_power'][i], rounding_precision)
            print(f"{energy_storage_system} run time = {run_time} tess setpoints are {tess_setpoints}")
        else:
            # Default case for other systems
            pass

        forecast_time = (datetime.now() + timedelta(hours=i)
                            ).replace(minute=0, second=0, microsecond=0)

        if method.lower() == "control":
            # algorithm
            message_dict[forecast_time] = {
                    "duration_in_second": 3600
                }
        elif method.lower() == "schedule":
            # configure
            message_dict[forecast_time] ={
                    f"{energy_storage_system}_setpoints": float(setpoints)
                }
        else:
            pass
        
    print(message_dict)
    

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

def update_schedule(self, setpoints):
    _hour = datetime.now().hour
    ld = []
    for ind in range(_hour, 24):
        ld.append(setpoints[ind])
    for ind in range(0, _hour):
        ld.append(setpoints[ind])
    return ld

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
    
def run_process(self):
        load = backward_fill_na(forward_fill_na(load))
        price = forward_fill_na(price)

        if method.lower() == "control":
            get_schedule_from_control()
            schedule_operations()

        elif method.lower() == "schedule":
            setpoints = update_schedule(setpoints)
            schedule_operations()
        elif method.lower() == "direct":
            print(f"Direct actuation")
        else:
            pass
    

_main function

run run_process


# class Main:
#     def __init__(self, config_path):
#         with open(config_path, 'r') as config_file:
#             contents = config_file.read()
#             while "/*" in contents:
#                 preComment, postComment = contents.split('/*', 1)
#                 contents = preComment + postComment.split('*/', 1)[1]
#             config = json.loads(contents.replace("'", '"'))

#         method = config.get('method', 1)
#         simulation_time = config.get('simulation_time')

#         results_db = pd.DataFrame()

#         for ts in simulation_time:
#             LM = Hot5(config=config, ts=ts)
#             CP = ChillerModel(config=config, ts=ts)
#             df = LM.adjust_hot_five()
#             df = CP.adjust_chiller_model(df, method)
#             df = df.loc[(df['Time'] >= ts)].reset_index(drop=True)
#             df['uncontrollable_power'] = 0
#             uc = pd.read_csv(config.get('uncontrol_file'))
#             for weekday in range(0, 7, 1):
#                 for hour in range(0, 24, 1):
#                     df.loc[(df['Time'].dt.weekday == weekday) & (df['Time'].dt.hour == hour), 'uncontrollable_power'] = float(uc.loc[(uc['weekday'] == weekday) & (uc['hour'] == hour), 'load'])
#             df['ice_mass'] = config['optimizer_config'].get('ice_mass', 1.5)
#             df = TesOptimization(df)
#             results_db = pd.concat([results_db, df]).reset_index(drop=True)
#             print(df)
#         results_db.to_csv(config.get('results_file', '') + 'method {}_control {}.csv'.format(method, config.get('control', '')))

#     def TesOptimization(self, df):
#         opt1 = opt(df, config)
#         df = opt1.run_opt()
#         return df