# -*- coding: utf-8 -*-
"""
Created on Thu Aug 24 16:04:00 2023

@author: awei
"""
from datetime import datetime
import argparse
from sqlalchemy import create_engine

import seaborn as sns
import matplotlib.pyplot as plt
import lightgbm as lgb
import pandas as pd
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import train_test_split

from __init__ import path
#from get_data import data_loading #, data_plate
from feature_engineering import feature_engineering_main
from base import base_connect_database

# Constants
#MODEL_PATH = f'{path}/checkpoint/prediction_stock_model.txt'
PREDICTION_PRICE_OUTPUT_CSV_PATH = f'{path}/data/prediction_stock_price_train.csv'
TRAIN_TABLE_NAME = 'prediction_stock_price_train'
EVAL_TABLE_NAME = 'eval_train'
TEST_SIZE = 0.15 #数据集分割中测试集占比
SEED = 2023 #保证最大值和最小值的数据部分保持一致

plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号


class StockTrainModel:
    def __init__(self, date_start, date_end):
        """
        Initialize StockTrainModel object, including feature engineering and database connection.
        """
        self.perform_feature_engineering = feature_engineering_main.PerformFeatureEngineering()
        self.model = None
        self.date_start = None
        self.date_end = None
        
    def load_dataset(self, date_range_bunch, test_size=TEST_SIZE, random_state=SEED):
        x, y = date_range_bunch.data, date_range_bunch.target
        x_df = pd.DataFrame(x, columns=date_range_bunch.feature_names)
        x_train, x_test, y_train, y_test = train_test_split(x_df, y, test_size=test_size, random_state=random_state)
        return x_train, x_test, y_train, y_test
    
    def feature_engineering_pipline(self, date_range_data):
        """
        Execute feature engineering pipeline and return datasets for training and testing.
        """
        date_range_high_bunch, date_range_low_bunch, date_range_diff_bunch = self.perform_feature_engineering.feature_engineering_dataset_pipline(date_range_data)
        x_train, x_test, y_high_train, y_high_test = self.load_dataset(date_range_high_bunch)

        _, _, y_low_train, y_low_test = self.load_dataset(date_range_low_bunch)
        
        _, _, y_diff_train, y_diff_test = self.load_dataset(date_range_diff_bunch)
        return x_train, x_test, y_high_train, y_high_test, y_low_train, y_low_test, y_diff_train, y_diff_test

    def data_processing_pipline(self, date_range_data):
        x_train, x_test, y_high_train, y_high_test, y_low_train, y_low_test, y_diff_train, y_diff_test = self.feature_engineering_pipline(date_range_data)
        
        # 训练集的主键删除，测试集的主键抛出
        del x_train['primaryKey']
        primary_key_test = x_test.pop('primaryKey').reset_index(drop=True)
        
        self.train_model(x_train, y_high_train, f'{path}/checkpoint/prediction_stock_high_model.txt')
        y_high, x_high_test, rmse, mae = self.evaluate_model(x_test, y_high_test, task_name='train', prediction_name='high')
        y_high = y_high.rename(columns={0: 'rearHighPctChgReal', 1: 'rearHighPctChgPred'})
        
        self.train_model(x_train, y_low_train, f'{path}/checkpoint/prediction_stock_low_model.txt')
        y_low, x_low_test, rmse, mae = self.evaluate_model(x_test, y_low_test, task_name='train', prediction_name='low')
        y_low = y_low.rename(columns={0: 'rearLowPctChgReal', 1: 'rearLowPctChgPred'})
        
        self.train_model(x_train, y_diff_train, f'{path}/checkpoint/prediction_stock_diff_model.txt')
        y_diff, x_diff_test, rmse, mae = self.evaluate_model(x_test, y_diff_test, task_name='train', prediction_name='diff')
        y_diff = y_diff.rename(columns={0: 'rearDiffPctChgReal', 1: 'rearDiffPctChgPred'})

        x_high_test = x_high_test.reset_index(drop=True)  # train_test_split过程中是保留了index的，在这一步重置index
        prediction_stock_price  = pd.concat([y_high, y_low, y_diff, x_high_test], axis=1)
        
        prediction_stock_price['remarks'] = prediction_stock_price.apply(lambda row: 'limit_up' if row['high'] == row['low'] else '', axis=1)
        
        prediction_stock_price = prediction_stock_price[['rearLowPctChgReal', 'rearLowPctChgPred', 'rearHighPctChgReal', 'rearHighPctChgPred',
                                                         'rearDiffPctChgReal', 'rearDiffPctChgPred', 'open','high', 'low', 'close','volume',
                                                         'amount','turn', 'pctChg', 'remarks']]
        # 通过主键关联字段
        related_name = ['date', 'code', 'code_name', 'isST']
        prediction_stock_price['primaryKey'] = primary_key_test
        
        prediction_stock_price_related = pd.merge(prediction_stock_price, date_range_data[['primaryKey']+related_name], on='primaryKey')
        
        with base_connect_database.engine_conn('postgre') as conn:
            prediction_stock_price_related.to_sql(TRAIN_TABLE_NAME, con=conn.engine, index=False, if_exists='replace')
        
        return prediction_stock_price_related

    def train_model(self, x_train, y_train, model_path):
        """
        Train LightGBM model.
        """
        params = {
            'task': 'train',
            'boosting': 'gbdt',
            'objective': 'regression',
            'num_leaves': 10, # 决策树上的叶子节点的数量，控制树的复杂度
            'learning_rate': 0.05,
            'metric': ['mae'], # 模型通过mae进行优化, root_mean_squared_error进行评估。, 'root_mean_squared_error'
            'verbose': -1, # 控制输出信息的详细程度，-1 表示不输出任何信息
        }

        # loading data
        lgb_train = lgb.Dataset(x_train, y_train)

        # fitting the model
        self.model = lgb.train(params, train_set=lgb_train)
        self.model.save_model(model_path)
    # Save and load model
    #stock_model.save_model(MODEL_PATH)
# =============================================================================
#     def evaluate_model2(self, lgb_train, params):
#         # 通过训练模型本身的参数输出对应的评估指标
#         # Use cross-validation to obtain evaluation results
#         cv_results = lgb.cv(params, lgb_train, nfold=5, metrics=params['metric'], verbose_eval=False)
# 
#         # Output the results
#         for metric in params['metric']:
#             avg_metric = f'average {metric} across folds'
#             print(f"{avg_metric}: {cv_results[f'{metric}-mean'][-1]:.2f} ± {cv_results[f'{metric}-stdv'][-1]:.2f}")
# 
#         return cv_results
# =============================================================================

    def evaluate_model(self, x_test_eval, y_test_eval, task_name=None, prediction_name=None):
        """
        Evaluate model performance, calculate RMSE and MAE, output results to the database, and return DataFrame.
        """
        y_pred = self.model.predict(x_test_eval)
        
        # accuracy check
        mse = mean_squared_error(y_test_eval, y_pred)
        rmse = mse ** (0.5)
        mae = mean_absolute_error(y_test_eval, y_pred)
        y_result = pd.DataFrame([y_test_eval,y_pred]).T.astype(float).round(3)
        
        insert_timestamp = datetime.now().strftime('%F %T')
        y_eval_dict = {'rmse': round(rmse,3),
                       'mae': round(mae,3),
                       'task_name': task_name,
                       'prediction_name': prediction_name,
                       'insert_timestamp': insert_timestamp,
                       }
        y_eval_df = pd.DataFrame([y_eval_dict], columns=y_eval_dict.keys())
        
        with base_connect_database.engine_conn('postgre') as conn:
            y_eval_df.to_sql(EVAL_TABLE_NAME, con=conn.engine, index=False, if_exists='append')
            
        return y_result, x_test_eval, rmse, mae

# =============================================================================
#     def save_model(self, model_path):
#         """
#         Save trained model to the specified path.
#         """
#         self.model.save_model(model_path)
# =============================================================================
        
    def plot_feature_importance(self):
        """
        Plot feature importance using Seaborn.
        """
        feature_importance_split = pd.DataFrame({
            'Feature': self.model.feature_name(),
            'Importance': self.model.feature_importance(importance_type='split'),
            'Type': 'split'
        })
        
        feature_importance_gain = pd.DataFrame({
            'Feature': self.model.feature_name(),
            'Importance': self.model.feature_importance(importance_type='gain'),
            'Type': 'gain'
        })

        feature_importance = pd.concat([feature_importance_split, feature_importance_gain], ignore_index=True)

        plt.figure(figsize=(12, 6))
        sns.barplot(x='Importance', y='Feature', data=feature_importance, hue='Type', palette="viridis", dodge=True)
        plt.title('Feature Importance')
        plt.show()
# =============================================================================
#     def plot_feature_importance(self):
#         """
#         Plot feature importance.
#         """
#         lgb.plot_importance(self.model, importance_type='split', figsize=(10, 6), title='Feature importance (split)')
#         lgb.plot_importance(self.model, importance_type='gain', figsize=(10, 6), title='Feature importance (gain)')
# =============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date_start', type=str, default='2022-03-01', help='进行训练的起始时间')
    parser.add_argument('--date_end', type=str, default='2023-03-01', help='进行训练的结束时间')
    args = parser.parse_args()

    print(f'进行训练的起始时间: {args.date_start}\n进行训练的结束时间: {args.date_end}')
    
    # Load date range data
    #date_range_data = data_loading.feather_file_merge(args.date_start, args.date_end)
    with base_connect_database.engine_conn('postgre') as conn:
        date_range_data = pd.read_sql(f"SELECT * FROM history_a_stock_k_data WHERE date >= '{args.date_start}' AND date < '{args.date_end}'", con=conn.engine)
    
    stock_model = StockTrainModel(date_start=args.date_start, date_end=args.date_end)
    prediction_stock_price_related = stock_model.data_processing_pipline(date_range_data)
    
    # Rename and save to CSV file
    prediction_stock_price_related_rename = prediction_stock_price_related.rename(columns={'open': '今开盘价格',
                                                                                     'high': '最高价',
                                                                                     'low': '最低价',
                                                                                     'close': '今收盘价',
                                                                                     'volume': '成交数量',
                                                                                     'amount': '成交金额',
                                                                                     'turn': '换手率',
                                                                                     'pctChg': '涨跌幅',
                                                                                     'rearLowPctChgReal': '明天_最低价幅_真实值',
                                                                                     'rearLowPctChgPred': '明天_最低价幅_预测值',
                                                                                     'rearHighPctChgReal': '明天_最高价幅_真实值',
                                                                                     'rearHighPctChgPred': '明天_最高价幅_预测值',
                                                                                     'rearDiffPctChgReal': '明天_变化价幅_真实值',
                                                                                     'rearDiffPctChgPred': '明天_变化价幅_预测值',
                                                                                     'remarks': '备注',
                                                                                     'date': '日期',
                                                                                    'code': '股票代码',
                                                                                    'code_name': '股票中文名称',
                                                                                    'isST': '是否ST',
                                                                                    })
    prediction_stock_price_related_rename.to_csv(PREDICTION_PRICE_OUTPUT_CSV_PATH, index=False)
    
    #stock_model.load_model(MODEL_PATH)

    # Plot feature importance
    #stock_model.plot_feature_importance()
    stock_model.plot_feature_importance()





#w×RMSE+(1−w)×MAE

# =============================================================================
#     Index(['dateDiff', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn',
#            'tradestatus', 'pctChg', 'isST', 'industry_交通运输', 'industry_休闲服务',
#            'industry_传媒', 'industry_公用事业', 'industry_其他', 'industry_农林牧渔',
#            'industry_化工', 'industry_医药生物', 'industry_商业贸易', 'industry_国防军工',
#            'industry_家用电器', 'industry_建筑材料', 'industry_建筑装饰', 'industry_房地产',
#            'industry_有色金属', 'industry_机械设备', 'industry_汽车', 'industry_电子',
#            'industry_电气设备', 'industry_纺织服装', 'industry_综合', 'industry_计算机',
#            'industry_轻工制造', 'industry_通信', 'industry_采掘', 'industry_钢铁',
#            'industry_银行', 'industry_非银金融', 'industry_食品饮料'],
#           dtype='object')
# =============================================================================
    
# =============================================================================
# RMSE (Root Mean Squared Error):
#     优点：对较大的误差更加敏感，因为它平方了每个误差值。
#     缺点：对于异常值更为敏感，因为平方可能会放大异常值的影响。
# MAE (Mean Absolute Error):
#     优点：对异常值不敏感，因为它使用的是误差的绝对值。
#     缺点：不像 RMSE 那样对大误差给予更大的权重。
# 选择哪个指标通常取决于你对模型误差的偏好。如果你更关注大误差，可能会选择使用 RMSE。如果你希望对所有误差都保持相对平等的关注，那么 MAE 可能是更好的选择。
# =============================================================================
