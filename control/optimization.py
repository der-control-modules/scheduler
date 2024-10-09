import pyomo.environ as pyo
from pyomo.environ import *
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from model.bess import BatteryEnergyStorageSystem
from model.tess import ThermalEnergyStorageSystem

class Optimization():
    def __init__(self, load, uncontrollable_load, price, config):
        self.load = load
        self.uncontrollable_load = uncontrollable_load
        self.cooling_load = [a - b for a, b in zip(load, uncontrollable_load)]
        self.energy_storage_system = config.get("energy_storage_system").lower()
        self.control_type = config.get("control_type", 3)
        self.peak_demand_limit = config.get("peak_demand_limit", None)
        
        demand_rate_config = config["demand_rate_config"]
        self.demand_charge = config.get("demand_charge", 10)
        self.demand_charge_daily = self.demand_charge/30.
        self.type_of_demand_rate = demand_rate_config.get("type_of_demand_rate", 'flat')

        self.prices = price if config.get('control', 3) == 3 else list(pd.read_csv('data/default_prices_sp.csv')['price'])
    
        self.window_length = config.get('window_length', 24)
        self.time_intervals = range(0, self.window_length)

        self.model = pyo.ConcreteModel()
                # Initialize BESS and TESS based on configuration
        if 'bess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            bess_config = config['bess_config']
            self.bess = BatteryEnergyStorageSystem(self.model, bess_config)

        if 'tess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            tess_config = config['tess_config']
            self.tess = ThermalEnergyStorageSystem(self.model, load, uncontrollable_load, tess_config)
        self.set_model_variable()
        
        if self.type_of_demand_rate.lower() == 'tou':
            self.peak_time_start = demand_rate_config.get("peak_time_start", 16)
            self.peak_time_end = demand_rate_config.get("peak_time_end", 21)
            self.first_partial_peak_start = demand_rate_config.get("first_partial_peak_start", 14)
            self.first_partial_peak_stop = demand_rate_config.get("first_partial_peak_stop", 16)
            self.second_partial_peak_start =  demand_rate_config.get("second_partial_peak_start", 21)
            self.second_partial_peak_stop = demand_rate_config.get("second_partial_peak_stop", 23)

            ##Total Demand rate
            #max demand summer $26.07
            demand_charge = demand_rate_config.get("demand_charge", 26.07)
            #max Peak Demand Summer $32.90
            peak_demand_rate = demand_rate_config.get("peak_demand_rate", 32.90)
            #max Part-Peak Demand Summer $6.81
            part_peak_demand_price = demand_rate_config.get("part_peak_demand_price", 6.81)
            ##Converting to daily demand charge
            self.demand_charge_daily = demand_charge/30.
            self.peak_demand_rate_daily = peak_demand_rate/30.
            self.part_peak_demand_price_daily = part_peak_demand_price/30.
        else:
            self.peak_time_start = demand_rate_config.get("peak_time_start", 16)
            self.peak_time_end = demand_rate_config.get("peak_time_end", 21)
            demand_charge = demand_rate_config.get("demand_charge", 26.07)
            self.demand_charge_daily = demand_charge/30.
    
    
    def update(self, load=None, uncontrollable_load=None, bess_soc=None, tess_soc=None, _hour=None):
        if _hour is None: 
            _hour = datetime.now().hour
        if load is not None and uncontrollable_load is not None:
            ld = []
            un_ld = []
            pr = []
            for ind in range(_hour, 24):
                ld.append(load[ind])
                un_ld.append(uncontrollable_load[ind])
                pr.append(self.prices[ind])
                
            for ind in range(0, _hour):
                ld.append(load[ind])
                un_ld.append(uncontrollable_load[ind])
                pr.append(self.prices[ind])
                
            self.load = ld
            self.prices = pr
            self.tess.uncontrollable_load = un_ld
            self.tess.cooling_load = [a - b for a, b in zip(ld, un_ld)]
        if tess_soc is not None:
            self.tess.initial_soc = tess_soc
            
        if bess_soc is not None:
            self.bess.initial_soc = bess_soc 
            #self.final_soc = soc
        
        self.set_model_variable()
        
        
    def set_model_variable(self):
        if 'bess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            self.bess.set_model_variables()
        if 'tess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            self.tess.set_model_variables()
        
        if self.control_type == 3:
            self.model.peak_power = pyo.Var(bounds=(None, None))
            if self.type_of_demand_rate == 'TOU':
                self.model.peak_power_during_peak_demand = pyo.Var(bounds=(0, max(self.load)))
                self.model.peak_power_during_partial_peak_demand = pyo.Var(bounds=(0, max(self.load)))
        self.model.total_power = pyo.Var(self.time_intervals, bounds=(0, None))
        
    def peak_limit_constraint(self, model, interval):
        """
        Constraint to ensure the total power does not exceed the peak demand limit.
        """
        if self.control_type == 3:
            return model.total_power[interval] <= model.peak_power
        elif self.control_type in [1, 2]:
            return model.total_power[interval] <= max(self.load)
        else:
            return model.total_power[interval] <= self.peak_demand_limit
        
    def demand_charge_constraint(self, model, interval):
        """
        Enforce demand charge constraints based on different peak periods.
        """
        if self.peak_time_start <= interval < self.peak_time_end:
            return model.total_power_consumption[interval] <= model.peak_power_during_peak_demand
        elif self.first_partial_peak_start <= interval < self.first_partial_peak_stop:
            return model.total_power_consumption[interval] <= model.peak_power_during_partial_peak_demand
        elif self.second_partial_peak_start <= interval < self.second_partial_peak_stop:
            return model.total_power_consumption[interval] <= model.peak_power_during_partial_peak_demand
        else:
            return pyo.Constraint.Skip
        
    
    def total_power_constraint(self, model, interval):
        """
        Define the total power constraint as the sum of optionally BESS power, TESS power, and building load.
        This method adjusts the total power calculation based on the configured energy storage systems.
        """
        # Initialize variables for BESS and TESS power to zero.
        bess_power = 0
        tess_power = 0

        # Check the energy storage system configuration and adjust BESS and TESS power variables accordingly.
        if 'bess' in self.energy_storage_system.lower():
            bess_power = -model.bess_power[interval]  # Subtracting BESS power as it's likely providing power back to the grid or load.
        if 'tess' in self.energy_storage_system.lower():
            tess_power = model.tess_power[interval]   # Adding TESS power as it contributes to the load consumption.

        # The total power consumption for the given interval is the sum of building load, BESS, and TESS power contributions.
        return model.total_power_consumption[interval] == bess_power + self.load[interval] + tess_power

    
    def apply_constraints(self):
        if 'bess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            self.bess.apply_constraints()
        if 'tess' in self.energy_storage_system or 'hybrid' in self.energy_storage_system:
            self.tess.apply_constraints()
        self.model.total_power_constraint = pyo.Constraint(self.time_intervals, rule=self.total_power_constraint)
        self.model.peak_limit_constraint = pyo.Constraint(self.time_intervals, rule=self.peak_limit_constraint)
        if self.type_of_demand_rate.lower() == 'tou':
            self.model.demand_charge_constraint = pyo.Constraint(self.time_intervals, rule=self.demand_charge_constraint)
        

    def obj_rule(self, model):
        obj_cost = 0
        if self.control_type == 3:
            obj_cost = obj_cost + self.demand_charge_daily * model.p_peak
        obj_cost = obj_cost + sum(self.prices[i] * (model.p_total[i]) for i in range(0, self.window_length))
        return obj_cost
    
    def get_pyomo_var_values(self, pyomo_var):
        """
        Retrieve values from a Pyomo variable across a specified range.

        Args:
        pyomo_var (pyo.Var): The Pyomo variable to retrieve values from.
        window_length (int): The length of the range to retrieve values over.

        Returns:
        list: A list of values from the Pyomo variable.
        """
        return [pyo.value(pyomo_var[j]) for j in range(self.window_length)]


    def run_opt(self):
        """
        Run the optimization model and extract results using a dedicated function for retrieving Pyomo variable values.

        Returns:
        dict: A dictionary containing all relevant optimization results.
        """
        # Set the objective function of the model
        self.model.obj = pyo.Objective(rule=self.obj_rule, sense=pyo.minimize)
        solver = pyo.SolverFactory('mindtpy')
        
        # Attempt to solve the model using the specified solver configuration
        try:
            solver.solve(self.model, mip_solver='glpk', nlp_solver='ipopt', tee=True)
        except ValueError as ve:
            print(f"ValueError during optimization: {ve}")
            print(self.model.pprint())
            raise
        except Exception as e:
            print(f"Exception during optimization: {e}")
            raise

        # Initialize the results dictionary using the utility function to retrieve variable values
        results = {
            'peak_load_prediction': pyo.value(self.model.peak_power),
            'total_power': self.get_pyomo_var_values(self.model.total_power),
            'cooling_load': self.cooling_load
        }
        
        # Conditionally add BESS and TESS data based on the configuration
        if 'bess' in self.energy_storage_system.lower():
            results['soc_prediction_bess'] = self.get_pyomo_var_values(self.model.state_of_charge)
            results['bess_power'] = self.get_pyomo_var_values(self.model.bess_power)

        if 'tess' in self.energy_storage_system.lower():
            results.update({
                'soc_prediction_tess': self.get_pyomo_var_values(self.model.tess_state_of_charge),
                'tess_power': self.get_pyomo_var_values(self.model.tess_power),
                'binary': self.get_pyomo_var_values(self.model.tess_binary),
                'tess_u_ch': self.get_pyomo_var_values(self.model.tess_charging),
                'tess_u_dis': self.get_pyomo_var_values(self.model.tess_discharging),
                'tess_u': self.get_pyomo_var_values(self.model.tess_energy_usage)
            })

        return results


