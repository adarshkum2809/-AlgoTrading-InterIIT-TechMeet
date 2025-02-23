#  Copyright (c) Microsoft Corporation.
#  Licensed under the MIT License.
import pickle
import sys
from pathlib import Path

import fire
import numpy as np
import pandas as pd
import qlib
from highfreq_ops import BFillNan
from highfreq_ops import Cut
from highfreq_ops import Date
from highfreq_ops import DayLast
from highfreq_ops import FFillNan
from highfreq_ops import get_calendar_day
from highfreq_ops import IsNull
from highfreq_ops import Select
from qlib.config import HIGH_FREQ_CONFIG
from qlib.config import REG_CN
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.evaluate import backtest as normal_backtest
from qlib.contrib.evaluate import risk_analysis
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.strategy.strategy import TopkDropoutStrategy
from qlib.data.data import Cal
from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.ops import Operators
from qlib.tests.data import GetData
from qlib.utils import exists_qlib_data
from qlib.utils import init_instance_by_config


class HighfreqWorkflow(object):
    SPEC_CONF = {
        "custom_ops": [DayLast, FFillNan, BFillNan, Date, Select, IsNull, Cut],
        "expression_cache": None,
    }

    MARKET = "all"
    BENCHMARK = "SH000300"

    start_time = "2020-09-15 00:00:00"
    end_time = "2021-01-18 16:00:00"
    train_end_time = "2020-11-30 16:00:00"
    test_start_time = "2020-12-01 00:00:00"

    DATA_HANDLER_CONFIG0 = {
        "start_time": start_time,
        "end_time": end_time,
        "fit_start_time": start_time,
        "fit_end_time": train_end_time,
        "instruments": MARKET,
        "infer_processors": [
            {
                "class": "HighFreqNorm",
                "module_path": "highfreq_processor",
                "kwargs": {},
            }
        ],
    }
    DATA_HANDLER_CONFIG1 = {
        "start_time": start_time,
        "end_time": end_time,
        "instruments": MARKET,
    }

    task = {
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "HighFreqHandler",
                    "module_path": "highfreq_handler",
                    "kwargs": DATA_HANDLER_CONFIG0,
                },
                "segments": {
                    "train": (start_time, train_end_time),
                    "test": (
                        test_start_time,
                        end_time,
                    ),
                },
            },
        },
        "dataset_backtest": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "HighFreqBacktestHandler",
                    "module_path": "highfreq_handler",
                    "kwargs": DATA_HANDLER_CONFIG1,
                },
                "segments": {
                    "train": (start_time, train_end_time),
                    "test": (
                        test_start_time,
                        end_time,
                    ),
                },
            },
        },
    }

    def _init_qlib(self):
        """initialize qlib"""
        # use yahoo_cn_1min data
        QLIB_INIT_CONFIG = {**HIGH_FREQ_CONFIG, **self.SPEC_CONF}
        provider_uri = QLIB_INIT_CONFIG.get("provider_uri")
        if not exists_qlib_data(provider_uri):
            print(f"Qlib data is not found in {provider_uri}")
            GetData().qlib_data(target_dir=provider_uri, interval="1min", region=REG_CN)
        qlib.init(**QLIB_INIT_CONFIG)

    def _prepare_calender_cache(self):
        """preload the calendar for cache"""

        # This code used the copy-on-write feature of Linux to avoid calculating the calendar multiple times in the subprocess
        # This code may accelerate, but may be not useful on Windows and Mac Os
        Cal.calendar(freq="1min")
        get_calendar_day(freq="1min")

    def get_data(self):
        """use dataset to get highreq data"""
        self._init_qlib()
        self._prepare_calender_cache()

        dataset = init_instance_by_config(self.task["dataset"])
        xtrain, xtest = dataset.prepare(["train", "test"])
        print(xtrain, xtest)

        dataset_backtest = init_instance_by_config(self.task["dataset_backtest"])
        backtest_train, backtest_test = dataset_backtest.prepare(["train", "test"])
        print(backtest_train, backtest_test)

        return

    def dump_and_load_dataset(self):
        """dump and load dataset state on disk"""
        self._init_qlib()
        self._prepare_calender_cache()
        dataset = init_instance_by_config(self.task["dataset"])
        dataset_backtest = init_instance_by_config(self.task["dataset_backtest"])

        ##=============dump dataset=============
        dataset.to_pickle(path="dataset.pkl")
        dataset_backtest.to_pickle(path="dataset_backtest.pkl")

        del dataset, dataset_backtest
        ##=============reload dataset=============
        with open("dataset.pkl", "rb") as file_dataset:
            dataset = pickle.load(file_dataset)

        with open("dataset_backtest.pkl", "rb") as file_dataset_backtest:
            dataset_backtest = pickle.load(file_dataset_backtest)

        self._prepare_calender_cache()
        ##=============reinit dataset=============
        dataset.init(
            handler_kwargs={
                "init_type": DataHandlerLP.IT_LS,
                "start_time": "2021-01-19 00:00:00",
                "end_time": "2021-01-25 16:00:00",
            },
            segment_kwargs={
                "test": (
                    "2021-01-19 00:00:00",
                    "2021-01-25 16:00:00",
                ),
            },
        )
        dataset_backtest.init(
            handler_kwargs={
                "start_time": "2021-01-19 00:00:00",
                "end_time": "2021-01-25 16:00:00",
            },
            segment_kwargs={
                "test": (
                    "2021-01-19 00:00:00",
                    "2021-01-25 16:00:00",
                ),
            },
        )

        ##=============get data=============
        xtest = dataset.prepare(["test"])
        backtest_test = dataset_backtest.prepare(["test"])

        print(xtest, backtest_test)
        return

    def get_high_freq_data(self, data_path):
        self._init_qlib()
        self._prepare_calender_cache()

        import os

        dataset = init_instance_by_config(self.task["dataset"])
        xtrain, xtest = dataset.prepare(["train", "test"])
        normed_feature = pd.concat([xtrain, xtest]).sort_index()
        dic = dict(tuple(normed_feature.groupby("instrument")))
        feature_path = os.path.join(data_path, "normed_feature/")
        if not os.path.exists(feature_path):
            os.makedirs(feature_path)
        for k, v in dic.items():
            v.to_pickle(feature_path + f"{k}.pkl")

        dataset_backtest = init_instance_by_config(self.task["dataset_backtest"])
        backtest_train, backtest_test = dataset_backtest.prepare(["train", "test"])
        backtest = pd.concat([backtest_train, backtest_test]).sort_index()
        backtest["date"] = backtest.index.map(lambda x: x[1].date())
        backtest.set_index("date", append=True, drop=True, inplace=True)
        dic = dict(tuple(backtest.groupby("instrument")))
        backtest_path = os.path.join(data_path, "backtest/")
        if not os.path.exists(backtest_path):
            os.makedirs(backtest_path)
        for k, v in dic.items():
            v.to_pickle(backtest_path + f"{k}.pkl.backtest")


if __name__ == "__main__":
    # fire.Fire(HighfreqWorkflow)
    data_path = "../data/"
    workflow = HighfreqWorkflow()
    workflow.get_high_freq_data(data_path)
