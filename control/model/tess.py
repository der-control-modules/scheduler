import pyomo.environ as pyo 
from pyomo.environ import *
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

class ThermalEnergyStorageSystem:
    """
    Class representing a Thermal Energy Storage System (TESS).
    This class models a thermal energy storage system with various configurations and optimization constraints.
    """

    def __init__(self, model, load, uncontrollable_load, config):
        """
        Initializes the TESS model with parameters for the building load, uncontrollable load, price, and configuration.
        
        Args:
            load (list): The total load of the building.
            uncontrollable_load (list): The uncontrollable portion of the building load.
            price (list): List of prices for energy consumption.
            config (dict): Configuration dictionary for the TESS system, chiller, and demand rates.
        """
        self.load = load
        self.uncontrollable_load = uncontrollable_load
        self.cooling_load = [bl - ul for bl, ul in zip(load, uncontrollable_load)]
        
        chiller_config = config['chiller_config']
        self.time_intervals = range(0, self.optimization_window)
        
        # Chiller configuration
        self.ice_mass = chiller_config.get('ice_mass')
        self.ice_charge_rate = chiller_config.get('ice_charge_rate')
        self.ice_discharge_rate = chiller_config.get('ice_discharge_rate')
        self.cop = chiller_config.get('COP', 3.5)

        # TESS configuration settings
        self.optimization_window = config.get('window_length', 24)
        self.initial_soc = config.get('initial_soc', 10)
        self.final_soc = config.get('soc_final', 10)
        self.max_soc = config.get('max_soc', 90)
        self.min_soc = config.get('min_soc', 10)
        self.efficiency = config.get('efficiency', 0.9)
        self.charging_coefficients = config['parameters'].get('p_coef', [])
        self.discharging_coefficients = config['parameters'].get('r_coef', [])

        # Storage and temperature configurations
        self.storage_capacity = config.get('q_stor', 1900)
        self.cf = config.get('cf', 3.915)
        self.chilled_water_temp = (config.get('t_cw_ch', 23) - 32) * 5 / 9
        self.freezer_temp = (config.get('t_fr', 32) - 32) * 5 / 9
        self.cooled_inlet_temp = (config.get('t_cc_in', 40) - 32) * 5 / 9
        self.min_building_power = config.get('building_power_min', 40)
        self.peak_demand_limit = config.get('peak_limit')

        # Create optimization model
        self.model = model
        self.set_model_variable()
        
        
    def set_model_variable(self):
        if self.control_type == 3:
            self.model.peak_power = pyo.Var(bounds=(None, None))
        self.model.total_power = pyo.Var(self.time_intervals, bounds=(0, None))
        self.model.state_of_charge = pyo.Var(self.time_intervals, bounds=(self.min_soc, self.max_soc), initialize=self.initial_soc)
        self.model.tess_energy_usage = pyo.Var(self.time_intervals, bounds=(None, None), initialize=0)
        self.model.tess_power = pyo.Var(self.time_intervals, initialize=0)
        self.model.tess_charging = pyo.Var(self.time_intervals, bounds=(0, None), initialize=0)
        self.model.tess_discharging = pyo.Var(self.time_intervals, bounds=(0, None), initialize=0)
        self.model.tess_binary = pyo.Var(self.time_intervals, domain=pyo.Binary, bounds=(0, 1), initialize=0)
        
    
    def poly(self, coefficients, variable, order=2):
        """
        Evaluate a polynomial function given coefficients and a variable.
        
        Args:
            coefficients (list): List of polynomial coefficients.
            variable (float): The variable to evaluate the polynomial at.
            order (int): The order of the polynomial (default: 2).
        
        Returns:
            float: The result of the polynomial evaluation.
        """
        result = None
        if isinstance(coefficients, np.ndarray):
            arr = np.array(range(1, len(coefficients)))
            result = coefficients[0] + np.sum(coefficients[1:] * (variable ** arr))
        elif order == 2:
            result = coefficients[0] + coefficients[1] * variable + coefficients[2] * variable ** 2
        elif order == 6:
            result = coefficients[0] + coefficients[1] * variable + coefficients[2] * variable ** 2 + \
                     coefficients[3] * variable ** 3 + coefficients[4] * variable ** 4 + coefficients[5] * variable ** 5 + coefficients[6] * variable ** 6
        elif order == 5:
            result = coefficients[0] + coefficients[1] * variable + coefficients[2] * variable ** 2 + \
                     coefficients[3] * variable ** 3 + coefficients[4] * variable ** 4 + coefficients[5] * variable ** 5
        elif order == 3:
            result = coefficients[0] + coefficients[1] * variable + coefficients[2] * variable ** 2 + coefficients[3] * variable ** 3
        else:
            arr = range(1, len(coefficients))
            result = coefficients[0] + np.sum(coefficients[1:] * (variable ** arr))

        return result

    def upper_bound(self, load, order=5):
        """
        Compute the upper bound using polynomial fitting for charging coefficients.
        
        Args:
            load (float): The load value.
            order (int): The order of the polynomial (default: 5).
        
        Returns:
            float: The computed upper bound.
        """
        return self.poly(self.charging_coefficients, load, order=order)

    def lower_bound(self, load, order=3):
        """
        Compute the lower bound using polynomial fitting for discharging coefficients.
        
        Args:
            load (float): The load value.
            order (int): The order of the polynomial (default: 3).
        
        Returns:
            float: The computed lower bound.
        """
        return self.poly(self.discharging_coefficients, load, order=order)
    
    #For the following constrains 
    """
    Args:
        model: The Pyomo model.
        interval (int): The current interval.
    
    Returns:
        Pyomo constraint: The SOC balance constraint.
    """

    def soc_constraint(self, model, interval):
        """
        Constraint for the state of charge (SOC) balance.
        """
        if interval == 0:
            return model.state_of_charge[interval] == self.initial_soc
        else:
            return model.state_of_charge[interval] == model.state_of_charge[interval - 1] - \
                   ((model.tess_energy_usage[interval - 1]) / self.storage_capacity) * 100

    def charging_discharging_constraint(self, model, interval):
        """
        Constraint for charging and discharging of the thermal storage system.
        """
        return model.tess_energy_usage[interval] == -model.tess_charging[interval] + model.tess_discharging[interval]

    def power_balance_constraint1(self, model, interval):
        """
        First power balance constraint, ensures total power equals the cooling load and chiller power usage.
        """
        return model.total_power[interval] == (-model.tess_energy_usage[interval] / self.cop) + self.load[interval]

    def power_balance_constraint2(self, model, interval):
        """
        Second power balance constraint, ensures total power is greater than or equal to the uncontrollable load.
        """
        return model.total_power[interval] >= self.uncontrollable_load[interval]


    def charging_upper_bound_constraint(self, model, interval):
        """
        Constraint to ensure that the charging power does not exceed the upper bound.
        """
        return model.tess_charging[interval] <= (1 - model.tess_binary[interval]) * self.upper_bound(model.state_of_charge[interval] / 100) * \
               self.ice_charge_rate * (self.freezer_temp - self.chilled_water_temp) * self.cf

    def discharging_upper_bound_constraint1(self, model, interval):
        """
        Constraint to ensure that the discharging power does not exceed the upper bound.
        """
        return model.tess_discharging[interval] <= model.tess_binary[interval] * self.lower_bound(model.state_of_charge[interval] / 100) * \
               self.ice_discharge_rate * (self.cooled_inlet_temp - self.freezer_temp) * self.cf
               
    def discharging_upper_bound_constraint2(self, model, interval):
        return model.tess_discharging[interval] <= model.tess_binary[interval] * (self.cooling_load[interval] * self.cop)

    def min_soc_constraint(self, model, interval):
        """
        Constraint to ensure that the state of charge does not fall below the min SOC.
        """
        return self.min_soc <= model.state_of_charge[interval]

    def max_soc_constraint(self, model, interval):
        """
        Constraint to ensure that the state of charge does not exceed the max SOC.
        """
        return model.state_of_charge[interval] <= self.max_soc

    def end_of_day_soc_constraint(self, model, interval):
        """
        Constraint to ensure that the SOC meets the final target at the end of the optimization window.
        """
        if interval == 23 or interval == 24:
            return model.state_of_charge[interval] >= self.final_soc
        else:
            return pyo.Constraint.Skip

    def apply_constraints(self):
        """
        Apply all constraints for the TESS model and add them to the Pyomo model.
        """
        self.model.soc_constraint = pyo.Constraint(self.time_intervals, rule=self.soc_constraint)
        self.model.charging_discharging_constraint = pyo.Constraint(self.time_intervals, rule=self.charging_discharging_constraint)
        self.model.power_balance_constraint1 = pyo.Constraint(self.time_intervals, rule=self.power_balance_constraint1)
        self.model.power_balance_constraint2 = pyo.Constraint(self.time_intervals, rule=self.power_balance_constraint2)
        self.model.charging_upper_bound_constraint = pyo.Constraint(self.time_intervals, rule=self.charging_upper_bound_constraint)
        self.model.discharging_upper_bound_constraint1 = pyo.Constraint(self.time_intervals, rule=self.discharging_upper_bound_constraint1)
        self.model.discharging_upper_bound_constraint2 = pyo.Constraint(self.time_intervals, rule=self.discharging_upper_bound_constraint2)
        self.model.min_soc_constraint = pyo.Constraint(self.time_intervals, rule=self.min_soc_constraint)
        self.model.max_soc_constraint = pyo.Constraint(self.time_intervals, rule=self.max_soc_constraint)
        self.model.end_of_day_soc_constraint = pyo.Constraint(self.time_intervals, rule=self.end_of_day_soc_constraint)
