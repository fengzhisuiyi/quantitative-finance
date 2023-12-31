# -*- coding: utf-8 -*-
"""
Created on Wed Nov 29 17:07:09 2023

@author: awei
特征工程主程序
feature_engineering_main
"""
import argparse

import numpy as np
import pandas as pd
from sklearn.preprocessing import OneHotEncoder
from sklearn.utils import Bunch

from __init__ import path
from base import base_connect_database, base_utils
pd.options.mode.chained_assignment = None

class PerformFeatureEngineering:
    def __init__(self):
        """
        初始化函数，用于登录系统和加载行业分类数据
        :param check:是否检修中间层date_range_data
        """
        try:
            conn_pg = base_connect_database.engine_conn('postgre')
        except Exception as e:
            print(f"数据库获取数据异常: {e}")
        
        trading_day_df = pd.read_sql('trade_datas', con=conn_pg.engine)
        self.trading_day_df = trading_day_df[trading_day_df.is_trading_day=='1']
        
        # 行业分类数据
        stock_industry = pd.read_sql('stock_industry', con=conn_pg.engine)
        #stock_industry.loc[stock_industry.industry.isnull(), 'industry'] = '其他' # 不能在这步补全，《行业分类数据》不够完整会导致industry为nan
        self.code_and_industry_dict = stock_industry.set_index('code')['industry'].to_dict()
        
        self.one_hot_encoder = OneHotEncoder(sparse_output=False)
        
    def specified_trading_day(self, pre_date_num=1):
        """
        获取指定交易日的字典，用于预测日期的计算
        :param pre_date_num: 前置日期数，默认为1
        :return: 字典，包含指定交易日和对应的前置日期
        """
        trading_day_df = self.trading_day_df
        trading_day_df['pre_date'] = np.insert(trading_day_df.calendar_date, 0, ['']*pre_date_num)[:-pre_date_num]
        trading_day_pre_dict = trading_day_df.set_index('calendar_date')['pre_date'].to_dict()
        return trading_day_pre_dict

    def create_values_to_predicted(self, date_range_data):
        """
        制作待预测值，为后一天的最高价和最低价
        :param date_range_data: 包含日期范围的DataFrame
        :return: 包含待预测值的DataFrame
        """
        # 待预测的指定交易日的主键、价格
        predict_pd = date_range_data[['target_date', 'code', 'high', 'low']]
        predict_pd['primary_key'] = (predict_pd['target_date']+predict_pd['code']).apply(base_utils.md5_str)
        predict_pd = predict_pd.rename(columns={'high': 'rear_high',
                                                'low': 'rear_low'})
        predict_pd = predict_pd[['primary_key', 'rear_high', 'rear_low']]
        
        # 关联对应后置最低最高价格
        date_range_data = pd.merge(date_range_data, predict_pd, on='primary_key')
        # print(date_range_data[date_range_data.code=='sz.399997'][['primaryKey', 'date','rearDate','high','rearHigh']]) #观察数据
        
        return date_range_data
    
    #def generate_dictionary
    
    def build_features(self, date_range_data):
        """
        构建数据集，将DataFrame转换为Bunch
        :param date_range_data: 包含日期范围的DataFrame
        :return: 包含数据集的Bunch
        """
        ## 训练特征
        
        # 特征: 基础_距离上一次开盘天数
        date_range_data['date_diff'] = (pd.to_datetime(date_range_data.target_date) - pd.to_datetime(date_range_data.date)).dt.days
        
        # 特征：基础_星期
        date_range_data['date_week'] = pd.to_datetime(date_range_data['date'], format='%Y-%m-%d').dt.day_name()
        
        # 特征: 宏观大盘_大盘成交量
        sh000001_map_dict = date_range_data[date_range_data.code=='sh.000001'][['date', 'amount']].set_index('date')['amount'].to_dict()
        date_range_data['macro_sh000001'] = date_range_data['date'].map(sh000001_map_dict)  # 上证综合指数
        
        sz399101_map_dict = date_range_data[date_range_data.code=='sz.399101'][['date', 'amount']].set_index('date')['amount'].to_dict()
        date_range_data['macro_sz399101'] = date_range_data['date'].map(sz399101_map_dict) # 中小企业综合指数 
        
        sz399102_map_dict = date_range_data[date_range_data.code=='sz.399102'][['date', 'amount']].set_index('date')['amount'].to_dict()
        date_range_data['macro_sz399102'] = date_range_data['date'].map(sz399102_map_dict) # 创业板综合指数
        
        sz399106_map_dict = date_range_data[date_range_data.code=='sz.399106'][['date', 'amount']].set_index('date')['amount'].to_dict()
        date_range_data['macro_sz399106'] = date_range_data['date'].map(sz399106_map_dict)  # 深证综合指数
        
        # 特征: 中观板块_行业
        date_range_data['industry'] = date_range_data.code.map(self.code_and_industry_dict)
        date_range_data['industry'] = date_range_data['industry'].replace(['', pd.NA], '其他')
        

        
        # lightgbm不支持str，把str类型转化为ont-hot
        date_range_data = pd.get_dummies(date_range_data, columns=['industry', 'tradestatus', 'isST', 'date_week'])
        
        feature_names = date_range_data.columns.tolist()
        
        # 明日最高/低值相对于今日收盘价的涨跌幅真实值
        date_range_data['rear_high_real'] = ((date_range_data['rear_high'] - date_range_data['close']) / date_range_data['close']) * 100
        date_range_data['rear_low_real'] = ((date_range_data['rear_low'] - date_range_data['close']) / date_range_data['close']) * 100
        date_range_data['rear_diff_real'] = date_range_data.rear_high_real - date_range_data.rear_low_real
        
        # 删除非训练字段
        columns_to_drop = ['date', 'code', 'code_name', 'adjustflag', 'target_date', 'rear_low', 'rear_high']
        feature_names = list(set(feature_names) - set(columns_to_drop))
        return date_range_data, feature_names
    
    def build_dataset(self, date_range_data, feature_names):
        # 构建数据集
        
        feature_names = sorted(feature_names) # 输出有序标签
        #print(f'feature_names_engineering:\n {feature_names}')
        feature_df = date_range_data[feature_names]
        
        target_names = ['rear_low_real', 'rear_high_real', 'rear_diff_real']
        date_range_dict = {'data': np.array(feature_df.to_records(index=False)),  # 不使用 feature_df.values,使用结构化数组保存每一列的类型
                         'feature_names': feature_names,
                         'target': date_range_data[target_names].values,  # 机器学习预测值
                         'target_names': [target_names],
                         }
        date_range_bunch = Bunch(**date_range_dict)

        return date_range_bunch
    
    def feature_engineering_pipline(self, date_range_data):
        """
        特征工程的主要流程，包括指定交易日、创建待预测值、构建数据集
        :param date_range_data: 包含日期范围的DataFrame
        :return: 包含数据集的Bunch
        """
        trading_day_target_dict = self.specified_trading_day(pre_date_num=1)
        date_range_data['target_date'] = date_range_data.date.map(trading_day_target_dict)
        date_range_data = self.create_values_to_predicted(date_range_data)
        
        # 构建数据集
        date_range_data, feature_names = self.build_features(date_range_data)
        return date_range_data, feature_names
    
    def feature_engineering_dataset_pipline(self, date_range_data):
        """
        特征工程的主要流程，包括指定交易日、创建待预测值、构建数据集
        :param date_range_data: 包含日期范围的DataFrame
        :return: 包含数据集的Bunch
        """
        # 构建数据集
        date_range_data, feature_names = self.feature_engineering_pipline(date_range_data)
        date_range_bunch = self.build_dataset(date_range_data, feature_names)
        return date_range_bunch
    
    
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date_start', type=str, default='2023-01-01', help='When to start feature engineering')
    parser.add_argument('--date_end', type=str, default='2023-03-01', help='End time for feature engineering')
    args = parser.parse_args()
    
    print(f'When to start feature engineering: {args.date_start}\nEnd time for feature engineering: {args.date_end}')
    
    # 获取日期段数据
    with base_connect_database.engine_conn('postgre') as conn:
        date_range_data = pd.read_sql(f"SELECT * FROM history_a_stock_k_data WHERE date >= '{args.date_start}' AND date < '{args.date_end}'", con=conn.engine)
    #date_range_data = data_loading.feather_file_merge(args.date_start, args.date_end)
    print(date_range_data)
    
    # 特征工程
    perform_feature_engineering = PerformFeatureEngineering()
    
    # 特征工程结果
    date_range_data, feature_names = perform_feature_engineering.feature_engineering_pipline(date_range_data)
    
    
    #date_range_high_bunch, date_range_low_bunch = perform_feature_engineering.feature_engineering_pipline(date_range_data)
    #print(date_range_high_bunch, date_range_low_bunch)
    

