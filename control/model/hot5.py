import pytz
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pandas.tseries.offsets import CustomBusinessDay, BDay
from pandas.tseries.holiday import USFederalHolidayCalendar


class Hot5:
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
        self.COP = self.parameters.get('COP')
        self.t_cw_norm = self.parameters.get('t_cw_norm')
        if type(self.t_cw_norm) != str:
            self.units[self.parameters['t_cw_norm']] = self.units['t_cw_norm']

        self.tz = self.config.get('timezone')
        self.bday_us = CustomBusinessDay(calendar=USFederalHolidayCalendar())

        self.aggregate_in_min = self.config.get('aggregate_in_min', 60)
        self.aggregate_freq = str(self.aggregate_in_min) + 'Min'

        ts = pd.to_datetime(ts) - timedelta(days=1)
        local_tz = pytz.timezone(self.tz)
        self.cur_time = local_tz.localize(datetime(ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second))

    def point_map(self, df):
        for key, value in self.point_mapping.items():
            df = df.rename(columns={value: key})
        return df

    def adjust_hot_five(self, days=11):
        while True:
            results = self.calculate_latest_baseline(cur_time=self.cur_time, days=days)
            if results is not None:
                break
            else:
                days += 1
        results = results.reset_index()
        results = results.rename(columns={'Data': 'Time',
                                          'hot5_pow_adj_avg': 'Predict',
                                          'CoolingLoad': 'Actual'})
        return results

    def unit_adjust(self, unit, series):
        if unit == 'f':
            series = (series - 32) * 5 / 9
        elif unit == 'gpm':
            series = series * 0.063
        elif unit == 'wh':
            series = series / 1000
        return series

    def call_historian(self, start_date_utc):
        df = pd.read_csv(self.database_file)
        df = self.point_map(df)
        df = self.load_calc(df)
        df[self.ts_name] = pd.to_datetime(df[self.ts_name], utc=True)
        df = df[df[self.ts_name] >= start_date_utc]
        return df

    def load_calc(self, df):
        if type(self.t_cw_norm) != str:
            sets = {'SupplyTemp', 'ReturnTemp', 'WaterMass', 'OAT'}
        else:
            sets = {'SupplyTemp', 'ReturnTemp', 'WaterMass', 'OAT', self.parameters.get('t_cw_norm')}

        if self.power_name in df.columns:
            print('Cooling load data already exists')
            unit = str.lower(self.power_name)
            df[self.power_name] = self.unit_adjust(unit, df[self.power_name])
            return df
        else:
            if sets.issubset(df.columns):
                for col in list(sets):
                    unit = str.lower(self.units[col])
                    df[col] = self.unit_adjust(unit, df[col])
                print('Calculate cooling load')
                df['CoolingLoad'] = df['WaterMass'] * (df['ReturnTemp'] - df['SupplyTemp']) * self.parameters.get('cf', 3.915)
                df.loc[(df['CoolingLoad'] < 0), 'CoolingLoad'] = 0
                return df
            else:
                print('Not enough data for calculate cooling load')
                return None

    def calculate_latest_baseline(self, cur_time, days):
        if type(self.t_cw_norm) != str:
            unit_points = [self.out_temp_name, self.power_name]
        else:
            unit_points = [self.out_temp_name, self.power_name, self.parameters.get('t_cw_norm')]
        df = None

        start_time = cur_time - days * self.bday_us
        start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date_utc = start_time.astimezone(pytz.utc)
        cur_time_utc = cur_time.astimezone(pytz.utc)
        df_extension = {}

        for point in unit_points:
            result = self.call_historian(start_date_utc)
            if len(result) > 0:
                df2 = pd.DataFrame(result, columns=[self.ts_name, point])
                df2[self.ts_name] = pd.to_datetime(df2[self.ts_name], utc=True)
                df2[point] = pd.to_numeric(df2[point])
                df2 = df2[df2[self.ts_name] < cur_time_utc]
                df2 = df2.groupby([pd.Grouper(key=self.ts_name, freq=self.aggregate_freq)]).mean()
                df = df2 if df is None else pd.merge(df, df2, how='outer', left_index=True, right_index=True)

            if not point in df_extension:
                df3 = pd.DataFrame(result, columns=[self.ts_name, point])
                df3[self.ts_name] = pd.to_datetime(df3[self.ts_name], utc=True)
                df3[point] = pd.to_numeric(df3[point])
                df3 = df3.loc[(df3[self.ts_name] >= cur_time_utc) & (df3[self.ts_name] < cur_time_utc + 2 * self.bday_us)]
                df3 = df3.groupby([pd.Grouper(key=self.ts_name, freq=self.aggregate_freq)]).mean()
                df3 = df3.reset_index()
                df_extension[point] = list(df3[point])
                df_extension[self.ts_name] = list(df3[self.ts_name])

        result_df = None

        if df is not None and not df.empty:
            df_extension = pd.DataFrame(df_extension)
            df_extension = df_extension.set_index([self.ts_name])
            df_extension = pd.concat([df, df_extension])
            result_df = self.calculate_baseline_logic(df_extension)
        return result_df

    def map_day(self, d):
        np_datetime = np.datetime64("{:02d}-{:02d}-{:02d}".format(d.year, d.month, d.day))
        if np_datetime in self.bday_us.holidays:
            return 8
        return d.weekday()

    def calculate_baseline_logic(self, dP):
        dP['DayOfWeek'] = dP.index.map(lambda v: self.map_day(v))
        if type(self.t_cw_norm) != str:
            dP.columns = [self.out_temp_name, self.power_name, "DayOfWeek"]
        else:
            dP.columns = [self.out_temp_name, self.power_name, self.parameters.get('t_cw_norm'), "DayOfWeek"]
        dP['year'] = dP.index.year
        dP['month'] = dP.index.month
        dP['hour'] = dP.index.hour
        dP['day'] = dP.index.day
        dP = dP[dP.DayOfWeek < 5]

        df = dP.resample('60min').mean()
        if type(self.t_cw_norm) != str:
            df = df.pivot_table(index=["year", "month", "day"], columns=["hour"], values=[self.out_temp_name, self.power_name])
        else:
            df = df.pivot_table(index=["year", "month", "day"], columns=["hour"], values=[self.out_temp_name, self.power_name, self.parameters.get('t_cw_norm')])
        self.save_4_debug(df, 'data1.csv')

        df_length = len(df.index)
        if df_length < 12:
            print('Not enough data to process')
            return None

        for j in range(0, 24):
            df['hot5_pow_avg', j] = None
        index_ = df['hot5_pow_avg'].index

        for i in range(10, df_length):
            for j in range(0, 24):
                df['hot5_pow_avg', j].loc[index_[i]] = df.iloc[i - 10:i - 1, :].sort_values([(self.out_temp_name, j)], ascending=False).head(5).iloc[:, (j + 0):(j + 1)].mean().iloc[0]
        self.save_4_debug(df, 'data2.csv')

        df = df.stack(level=['hour'])
        df = df.dropna()
        dq = df.reset_index()
        dq['Data'] = pd.to_datetime(
            dq.year.astype(int).apply(str) + '/' + dq.month.astype(int).apply(str) + '/' + dq.day.astype(int).apply(
                str) + ' ' + dq.hour.astype(int).apply(str) + ":00", format='%Y/%m/%d %H:%M')
        dq = dq.set_index(['Data'])
        dq = dq.drop(['year', 'month', 'day', 'hour'], axis=1)
        self.save_4_debug(dq, 'data3.csv')

        dq_length = len(dq.index) - 4
        dq["Adj2"] = 1.0
        for i in range(0, dq_length):
            dq['Adj2'][i + 4] = (dq[self.power_name][i:i+3].mean()) / (dq['hot5_pow_avg'][i:i+3].mean())
        self.save_4_debug(dq, 'data4.csv')

        dq.loc[dq['Adj2'] < 0.6, 'Adj2'] = 0.6
        dq.loc[dq['Adj2'] > 1.4, 'Adj2'] = 1.4
        dq['hot5_pow_adj_avg'] = dq['hot5_pow_avg'] * dq['Adj2']
        self.save_4_debug(dq, 'method1_result.csv')
        return dq

    def save_4_debug(self, df, name):
        if self.results_file is not None:
            try:
                df.to_csv(self.results_file + name)
            except Exception as ex:
                print(ex)