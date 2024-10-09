import pytz
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pandas.tseries.offsets import CustomBusinessDay, BDay
from pandas.tseries.holiday import USFederalHolidayCalendar


class ChillerModel:
    def __init__(self, config, ts):
        self.config = config
        self.ts_name = 'Time'
        self.out_temp_name = 'OAT'
        self.power_name = 'CoolingLoad'

        self.database_file = self.config.get('database_file')
        self.results_file = self.config.get('results_file')

        self.point_mapping = self.config.get('point_mapping')
        self.units = self.config.get('units')
        self.parameters = self.config.get('parameters')

        self.a_coef = self.parameters.get('a_coef')
        self.b_coef = self.parameters.get('b_coef')
        self.c_coef = self.parameters.get('c_coef')
        self.COP = self.parameters.get('COP')
        self.t_cw_norm = self.parameters.get('t_cw_norm')
        self.Q_nom = self.parameters.get('Q_nom')
        self.method = self.config.get('method')


    def point_map(self, df):
        for key, value in self.point_mapping.items():
            df = df.rename(columns={value: key})
        return df

    def poly(self, c, x, order=2):
        result = None
        if isinstance(c, np.ndarray):
            arr = np.array(range(1, len(c)))
            result = c[0] + np.sum(c[1:] * (x ** arr))
        elif order == 2:
            result = c[0] + c[1] * x + c[2] * x ** 2
        elif order == 6:
            result = c[0] + c[1] * x + c[2] * x ** 2 + c[3] * x ** 3 + c[4] * x ** 4 + c[5] * x ** 5 + c[6] * x ** 6
        else:
            arr = range(1, len(c))
            result = c[0] + np.sum(c[1:] * (x ** arr))
        return result

    def sigma_1(self, T_cw, t_out, coef):
        result = (coef[0] +
                  coef[1] * T_cw +
                  coef[2] * T_cw ** 2 +
                  coef[3] * t_out +
                  coef[4] * t_out ** 2 +
                  coef[5] * T_cw * t_out)
        return result

    def sigma_3(self, PLR):
        return self.poly(self.c_coef, PLR)

    def Q_avail(self, T_cw, t_out):
        return self.Q_nom * self.sigma_1(T_cw, t_out, self.a_coef)

    def P_chiller(self, q_chiller, T_cw, t_out):
        q_avail = self.Q_avail(T_cw, t_out)
        sigma_1 = self.sigma_1(T_cw, t_out, self.b_coef)
        sigma_3 = self.sigma_3(q_chiller / q_avail)
        p_chiller = q_avail * sigma_1 * sigma_3 / self.COP
        return p_chiller

    def adjust_chiller_model(self, df, method):
        df['chiller_power'] = 0
        if method == 1:
            if type(self.t_cw_norm) != str:
                for i in range(len(df)):
                    df['chiller_power'][i] = self.P_chiller(df['Predict'][i], (self.t_cw_norm - 32) * 5 / 9, df['OAT'][i])
            else:
                for i in range(len(df)):
                    df['chiller_power'][i] = self.P_chiller(df['Predict'][i], df[self.parameters.get('t_cw_norm')][i], df['OAT'][i])
        elif method == 2:
            df['chiller_power'] = df['Predict'] / self.COP
        return df
