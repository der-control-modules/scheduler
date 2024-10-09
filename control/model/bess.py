import pyomo.environ as pyo
import pandas as pd
from datetime import datetime, timedelta

class BatteryEnergyStorageSystem():
    """
    A class to model and optimize the operation of a Battery Energy Storage System (BESS).
    """

    def __init__(self, model, config):
        """
        Initialize the Battery Energy Storage System with given configurations.
        
        Args:
        - building_load (list): Building load for each time interval.
        - electricity_price (list): Price of electricity for each time interval.
        - config (dict): Configuration parameters for the BESS.
        """
        self.window_length = config.get("window_length", 24)
        self.rated_power_kw = config.get("rated_kw", 100.)
        self.rated_energy_kwh = config.get("rated_kwh", 200)
        self.min_building_power = config.get("building_power_min", 0.)
        self.max_charging_power = config.get("max_charging_power", 100.)
        self.max_discharging_power = config.get("max_discharging_power", 100.)
        self.charging_efficiency = config.get("charging_efficiency", 0.925)
        self.discharging_efficiency = config.get("discharging_efficiency", 0.975)
        self.target_soc = config.get("reference_soc", 50.)
        self.initial_soc = config.get("initial_soc", 50.)
        self.min_soc = config.get("min_soc", 20)
        self.max_soc = config.get("max_soc", 80)
        self.time_intervals = range(0, self.window_length)

        # Create Pyomo model
        self.model = model
        self.model.bess_discharging_power = pyo.Var(self.time_intervals, bounds=(0, self.rated_power_kw))
        self.model.bess_charging_power = pyo.Var(self.time_intervals, bounds=(0, self.rated_power_kw))
        self.model.bess_power = pyo.Var(self.time_intervals)
        self.model.total_power_consumption = pyo.Var(self.time_intervals, bounds=(0, None))
        self.model.state_of_charge = pyo.Var(self.time_intervals, bounds=(self.min_soc, self.max_soc))
        self.model.charge_status_binary = pyo.Var(self.time_intervals, domain=pyo.Binary, bounds=(0, 1), initialize=0)

    def set_model_variable(self):
        self.model = pyo.ConcreteModel()
        self.model.bess_discharging_power = pyo.Var(self.time_intervals, bounds=(0, self.rated_power_kw))
        self.model.bess_charging_power = pyo.Var(self.time_intervals, bounds=(0, self.rated_power_kw))
        self.model.bess_power = pyo.Var(self.time_intervals)
        self.model.bess_power_with_losses = pyo.Var(self.time_intervals)
        self.model.total_power_consumption = pyo.Var(self.time_intervals, bounds=(0, None))
        self.model.state_of_charge = pyo.Var(self.time_intervals, bounds=(self.min_soc, self.max_soc))
        self.model.charge_status_binary = pyo.Var(self.time_intervals, domain=pyo.Binary, bounds=(0, 1), initialize=0)

    def soc_constraint(self, model, interval):
        """
        Define the state of charge (SOC) constraint for the BESS model.
        """
        if interval == 0:
            return model.state_of_charge[interval] == self.initial_soc
        else:
            return model.state_of_charge[interval] == model.state_of_charge[interval - 1] - (model.bess_power_with_losses[interval - 1] / self.rated_energy_kwh) * 100

    def bess_power_constraint(self, model, interval):
        """
        Define the BESS power constraint based on charging and discharging power.
        """
        return model.bess_power[interval] == - model.bess_charging_power[interval] + model.bess_discharging_power[interval]

    def power_loss_constraint(self, model, interval):
        """
        Define the power loss constraint based on charging and discharging efficiencies.
        """
        return model.bess_power_with_losses[interval] == -self.charging_efficiency * model.bess_charging_power[interval] + (1 / self.discharging_efficiency) * model.bess_discharging_power[interval]

    def min_total_power_constraint(self, model, interval):
        """
        Enforce min total power constraint.
        """
        return model.total_power_consumption[interval] >= self.min_building_power

    def max_total_power_constraint(self, model, interval):
        """
        Enforce max total power constraint.
        """
        return model.total_power_consumption[interval] <= model.peak_building_power

    def min_soc_constraint(self, model, interval):
        """
        Enforce min state of charge constraint.
        """
        return self.min_soc <= model.state_of_charge[interval]

    def max_soc_constraint(self, model, interval):
        """
        Enforce max state of charge constraint.
        """
        return model.state_of_charge[interval] <= self.max_soc

    def min_bess_charging_power_constraint(self, model, interval):
        """
        Enforce min charging power constraint for the BESS.
        """
        return 0 <= model.bess_charging_power[interval]

    def max_bess_charging_power_constraint(self, model, interval):
        """
        Enforce max charging power constraint for the BESS.
        """
        return model.bess_charging_power[interval] <= self.max_charging_power * model.charge_status_binary[interval]

    def min_bess_discharging_power_constraint(self, model, interval):
        """
        Enforce min discharging power constraint for the BESS.
        """
        return 0 <= model.bess_discharging_power[interval]

    def max_bess_discharging_power_constraint(self, model, interval):
        """
        Enforce max discharging power constraint for the BESS.
        """
        return model.bess_discharging_power[interval] <= self.max_discharging_power * (1 - model.charge_status_binary[interval])

    def final_soc_constraint_eq1(self, model, interval):
        """
        Enforce final SOC constraint where the final SOC should equal the reference SOC.
        """
        if interval == 23:
            return model.state_of_charge[interval] == self.target_soc
        else:
            return pyo.Constraint.Skip
    
    def final_soc_constraint_eq2(self, model, interval):
        """
        Enforce alternative final SOC constraint with power loss consideration.
        """
        if interval == 23:
            return model.state_of_charge[interval] - (model.bess_power_with_losses[interval] / self.rated_energy_kwh) * 100 == self.target_soc
        else:
            return pyo.Constraint.Skip

    def apply_constraints(self):
        """
        Apply all constraints to the Pyomo model.
        """
        self.model.soc_constraint = pyo.Constraint(self.time_intervals, rule=self.soc_constraint)
        self.model.bess_power_constraint = pyo.Constraint(self.time_intervals, rule=self.bess_power_constraint)
        self.model.power_loss_constraint = pyo.Constraint(self.time_intervals, rule=self.power_loss_constraint)
        self.model.min_total_power_constraint = pyo.Constraint(self.time_intervals, rule=self.min_total_power_constraint)
        self.model.max_total_power_constraint = pyo.Constraint(self.time_intervals, rule=self.max_total_power_constraint)
        self.model.min_soc_constraint = pyo.Constraint(self.time_intervals, rule=self.min_soc_constraint)
        self.model.max_soc_constraint = pyo.Constraint(self.time_intervals, rule=self.max_soc_constraint)
        self.model.min_bess_charging_power_constraint = pyo.Constraint(self.time_intervals, rule=self.min_bess_charging_power_constraint)
        self.model.max_bess_charging_power_constraint = pyo.Constraint(self.time_intervals, rule=self.max_bess_charging_power_constraint)
        self.model.min_bess_discharging_power_constraint = pyo.Constraint(self.time_intervals, rule=self.min_bess_discharging_power_constraint)
        self.model.max_bess_discharging_power_constraint = pyo.Constraint(self.time_intervals, rule=self.max_bess_discharging_power_constraint)
        self.model.final_soc_constraint_eq1 = pyo.Constraint(self.time_intervals, rule=self.final_soc_constraint_eq1)
        self.model.final_soc_constraint_eq2 = pyo.Constraint(self.time_intervals, rule=self.final_soc_constraint_eq2)
        
