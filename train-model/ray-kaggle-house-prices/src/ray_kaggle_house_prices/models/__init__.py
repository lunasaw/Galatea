"""House Prices 模型族、特征工程和可持久化集成。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.base import BaseEstimator, RegressorMixin, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.kernel_ridge import KernelRidge
from sklearn.linear_model import ElasticNet, Lasso, Ridge
from sklearn.svm import SVR
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler


class CatBoostSklearnAdapter(BaseEstimator, RegressorMixin):
    """为旧版 CatBoost 提供兼容 sklearn 1.9 标签协议的适配器。"""

    def __init__(self, **parameters: Any) -> None:
        self.parameters = parameters

    def get_params(self, deep: bool = True) -> dict[str, Any]:
        return dict(self.parameters)

    def set_params(self, **parameters: Any) -> "CatBoostSklearnAdapter":
        self.parameters.update(parameters)
        return self

    def fit(self, X: Any, y: Any) -> "CatBoostSklearnAdapter":
        from catboost import CatBoostRegressor

        self.model_ = CatBoostRegressor(**self.parameters)
        self.model_.fit(X, y)
        return self

    def predict(self, X: Any) -> np.ndarray:
        return np.asarray(self.model_.predict(X), dtype="float64")


class CatBoostRawAdapter(CatBoostSklearnAdapter):
    """在原始类别列上训练 CatBoost，避免把类别关系稀释成独立哑变量。"""

    def fit(self, X: pd.DataFrame, y: Any) -> "CatBoostRawAdapter":
        from catboost import CatBoostRegressor

        frame = X.copy()
        self.feature_columns_ = list(frame.columns)
        self.categorical_columns_ = frame.select_dtypes(
            include=["object", "string", "category"]
        ).columns.tolist()
        for column in self.categorical_columns_:
            frame[column] = frame[column].astype("string").fillna("Missing").astype(str)
        self.model_ = CatBoostRegressor(**self.parameters)
        self.model_.fit(frame, y, cat_features=self.categorical_columns_)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        frame = X.reindex(columns=self.feature_columns_).copy()
        for column in self.categorical_columns_:
            frame[column] = frame[column].astype("string").fillna("Missing").astype(str)
        return np.asarray(self.model_.predict(frame), dtype="float64")


# 这些列的整数编码本质上是类别，而不是连续距离。
CATEGORICAL_NUMERIC_COLUMNS = ("MSSubClass",)
CATEGORICAL_NUMERIC_COPIES = ("OverallCond", "MoSold", "YrSold")

# 缺失值在原始数据中明确代表“没有该设施”，不能用众数设施替代。
STRUCTURAL_CATEGORICAL_COLUMNS = (
    "Alley", "BsmtQual", "BsmtCond", "BsmtExposure", "BsmtFinType1", "BsmtFinType2",
    "FireplaceQu", "GarageType", "GarageFinish", "GarageQual", "GarageCond",
    "MasVnrType", "PoolQC", "Fence", "MiscFeature",
)
STRUCTURAL_NUMERIC_COLUMNS = (
    "MasVnrArea", "BsmtFinSF1", "BsmtFinSF2", "BsmtUnfSF", "TotalBsmtSF",
    "BsmtFullBath", "BsmtHalfBath", "GarageYrBlt", "GarageCars", "GarageArea",
    "Fireplaces", "PoolArea", "WoodDeckSF", "OpenPorchSF",
    "EnclosedPorch", "3SsnPorch", "ScreenPorch",
)

# 质量、完成度和功能字段的业务顺序编码，同时保留原始类别字段。
ORDINAL_MAPS: dict[str, dict[str, int]] = {
    "ExterQual": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1},
    "ExterCond": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1},
    "BsmtQual": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1, "None": 0},
    "BsmtCond": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1, "None": 0},
    "HeatingQC": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1},
    "KitchenQual": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1},
    "FireplaceQu": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1, "None": 0},
    "GarageQual": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1, "None": 0},
    "GarageCond": {"Ex": 5, "Gd": 4, "TA": 3, "Fa": 2, "Po": 1, "None": 0},
    "PoolQC": {"Ex": 4, "Gd": 3, "TA": 2, "Fa": 1, "None": 0},
    "BsmtExposure": {"Gd": 4, "Av": 3, "Mn": 2, "No": 1, "None": 0},
    "BsmtFinType1": {"GLQ": 6, "ALQ": 5, "BLQ": 4, "Rec": 3, "LwQ": 2, "Unf": 1, "None": 0},
    "BsmtFinType2": {"GLQ": 6, "ALQ": 5, "BLQ": 4, "Rec": 3, "LwQ": 2, "Unf": 1, "None": 0},
    "GarageFinish": {"Fin": 3, "RFn": 2, "Unf": 1, "None": 0},
    "Functional": {"Typ": 7, "Min1": 6, "Min2": 5, "Mod": 4, "Maj1": 3, "Maj2": 2, "Sev": 1, "Sal": 0},
    "PavedDrive": {"Y": 2, "P": 1, "N": 0},
    "LandSlope": {"Gtl": 2, "Mod": 1, "Sev": 0},
    "LotShape": {"Reg": 3, "IR1": 2, "IR2": 1, "IR3": 0},
    "Utilities": {"AllPub": 3, "NoSewr": 2, "NoSeWa": 1, "ELO": 0},
}

# 对右偏、非负的面积/数量字段添加参数无关的 log1p 视图，专门增强线性模型。
SKEWED_NONNEGATIVE_COLUMNS = (
    "LotArea", "LotFrontage", "MasVnrArea", "BsmtFinSF1", "BsmtFinSF2", "BsmtUnfSF",
    "TotalBsmtSF", "1stFlrSF", "2ndFlrSF", "LowQualFinSF", "GrLivArea", "GarageArea",
    "WoodDeckSF", "OpenPorchSF", "EnclosedPorch", "3SsnPorch", "ScreenPorch", "PoolArea",
    "MiscVal", "TotalSF", "TotalFinSF", "TotalPorchSF", "TotalOutdoorArea",
)


class HouseFeatureEngineer(BaseEstimator, TransformerMixin):
    """执行确定性房屋特征工程，并按模型族选择线性专用视图。"""

    def __init__(self, include_linear_views: bool = True) -> None:
        self.include_linear_views = include_linear_views

    def fit(self, X: pd.DataFrame, y: Any = None) -> "HouseFeatureEngineer":
        # LotFrontage 的邻域统计只从当前折训练数据拟合，防止验证/推理信息回流。
        self.feature_names_in_ = np.asarray(X.columns, dtype=object)
        frontage_values = pd.to_numeric(X.get("LotFrontage", pd.Series(dtype="float64")), errors="coerce")
        frontage_median = float(frontage_values.median()) if "LotFrontage" in X else 0.0
        self._lot_frontage_global_ = frontage_median if np.isfinite(frontage_median) else 0.0
        self._lot_frontage_by_neighborhood_ = {}
        if {"LotFrontage", "Neighborhood"}.issubset(X.columns):
            frontage = pd.to_numeric(X["LotFrontage"], errors="coerce")
            grouped = pd.DataFrame({"frontage": frontage, "neighborhood": X["Neighborhood"]}).dropna(subset=["frontage"])
            self._lot_frontage_by_neighborhood_ = {
                str(key): float(value) for key, value in grouped.groupby("neighborhood")["frontage"].median().items()
            }
        # 仅在线性分支按当前折训练数据学习右偏、非负连续变量，避免固定阈值跨折泄漏。
        self._log_columns_ = ()
        base = self._transform(X)
        if self.include_linear_views:
            excluded_tokens = ("Ordinal", "Missing", "Category")
            candidates: list[str] = []
            for column in base.select_dtypes(include=["number"]).columns:
                values = pd.to_numeric(base[column], errors="coerce").dropna()
                if (
                    column in {"SaleYear", "YrSold", "YearBuilt", "YearRemodAdd", "GarageYrBlt"}
                    or any(token in column for token in excluded_tokens)
                    or values.nunique() <= 2
                    or float(values.min()) < 0.0
                ):
                    continue
                skew = float(values.skew()) if len(values) > 2 else 0.0
                if np.isfinite(skew) and skew > 0.75:
                    candidates.append(column)
            self._log_columns_ = tuple(dict.fromkeys((*SKEWED_NONNEGATIVE_COLUMNS, *candidates)))
        transformed = self._transform(X)
        self.feature_names_out_ = np.asarray(transformed.columns, dtype=object)
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        return self._transform(X)

    def _transform(self, X: pd.DataFrame) -> pd.DataFrame:
        frame = X.copy()
        if "SalePrice" in frame or "Id" in frame:
            raise ValueError("特征工程输入不得包含 SalePrice 或 Id")

        # Ames 数据的结构性缺失表示“没有设施”；显式保留该语义，避免众数插补制造假设施。
        for column in STRUCTURAL_CATEGORICAL_COLUMNS:
            if column in frame:
                frame[column] = frame[column].astype("string").fillna("None")
        if "LotFrontage" in frame:
            raw_frontage = pd.to_numeric(frame["LotFrontage"], errors="coerce")
            frame["LotFrontageMissing"] = raw_frontage.isna().astype("int8")
            frontage = raw_frontage
            if "Neighborhood" in frame:
                neighborhood_median = frame["Neighborhood"].astype("string").map(self._lot_frontage_by_neighborhood_)
                frontage = frontage.fillna(neighborhood_median)
            frame["LotFrontage"] = frontage.fillna(self._lot_frontage_global_)
        for column in STRUCTURAL_NUMERIC_COLUMNS:
            if column in frame:
                raw_numeric = pd.to_numeric(frame[column], errors="coerce")
                if self.include_linear_views:
                    frame[f"{column}Missing"] = raw_numeric.isna().astype("int8")
                frame[column] = raw_numeric.fillna(0.0)
        for column in CATEGORICAL_NUMERIC_COLUMNS:
            if column in frame:
                frame[column] = frame[column].fillna(-1).astype("int64").astype("string")
        for column in CATEGORICAL_NUMERIC_COPIES:
            if column in frame:
                frame[f"{column}_Category"] = frame[column].fillna(-1).astype("int64").astype("string")

        def numeric(column: str) -> pd.Series:
            if column not in frame:
                return pd.Series(0.0, index=frame.index)
            return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

        def total(name: str, columns: tuple[str, ...]) -> None:
            frame[name] = sum((numeric(column) for column in columns), pd.Series(0.0, index=frame.index))

        # 面积、设施和年龄特征将房屋结构先验直接提供给线性与树模型。
        total("TotalSF", ("TotalBsmtSF", "1stFlrSF", "2ndFlrSF"))
        total("TotalFinSF", ("BsmtFinSF1", "BsmtFinSF2", "1stFlrSF", "2ndFlrSF"))
        total("TotalBsmtFinSF", ("BsmtFinSF1", "BsmtFinSF2"))
        frame["TotalBathrooms"] = numeric("FullBath") + 0.5 * numeric("HalfBath") + numeric("BsmtFullBath") + 0.5 * numeric("BsmtHalfBath")
        total("TotalPorchSF", ("OpenPorchSF", "3SsnPorch", "EnclosedPorch", "ScreenPorch", "WoodDeckSF"))
        # TotalPorchSF 已经包含 WoodDeckSF，这里不能重复计入。
        total("TotalOutdoorArea", ("GarageArea", "PoolArea", "OpenPorchSF", "3SsnPorch", "EnclosedPorch", "ScreenPorch", "WoodDeckSF"))
        total("TotalHouseArea", ("TotalSF", "GarageArea", "TotalPorchSF"))
        frame["TotalRoomsPlusBath"] = numeric("TotRmsAbvGrd") + numeric("FullBath") + numeric("HalfBath")
        frame["TotalGarageValue"] = numeric("GarageCars") * numeric("GarageArea")

        sold_year = pd.to_numeric(frame["YrSold"], errors="coerce") if "YrSold" in frame else pd.Series(0.0, index=frame.index)
        if "YearBuilt" in frame:
            frame["AgeAtSale"] = (sold_year - numeric("YearBuilt")).clip(lower=0)
        if "YearRemodAdd" in frame:
            frame["YearsSinceRemodel"] = (sold_year - numeric("YearRemodAdd")).clip(lower=0)
            frame["IsRemodeled"] = (numeric("YearRemodAdd") > numeric("YearBuilt")).astype("int8")
        if "GarageYrBlt" in frame:
            garage_year = numeric("GarageYrBlt")
            frame["GarageAge"] = (sold_year - garage_year).where(garage_year > 0, 0).clip(lower=0)
            frame["HasGarageYear"] = (garage_year > 0).astype("int8")

        for output, source in (
            ("HasPool", "PoolArea"),
            ("HasGarage", "GarageArea"),
            ("HasBasement", "TotalBsmtSF"),
            ("HasFireplace", "Fireplaces"),
            ("HasSecondFloor", "2ndFlrSF"),
            ("HasMasonryVeneer", "MasVnrArea"),
            ("HasWoodDeck", "WoodDeckSF"),
        ):
            if source in frame:
                frame[output] = (numeric(source) > 0).astype("int8")

        # 质量/完成度字段既保留类别原值，也提供有序数值视图。
        for column, mapping in ORDINAL_MAPS.items():
            if column in frame:
                values = frame[column].astype("string").fillna("None")
                frame[f"{column}_Ordinal"] = values.map(mapping).fillna(-1).astype("float64")

        # 低基数类别交叉项让线性模型直接表达“社区×质量”等强组合效应。
        for left, right in (
            ("Neighborhood", "OverallQual"),
            ("KitchenQual", "OverallQual"),
            ("ExterQual", "OverallQual"),
            ("GarageType", "GarageFinish"),
            ("BsmtQual", "BsmtExposure"),
            ("Exterior1st", "ExterQual"),
        ):
            if left in frame and right in frame:
                frame[f"{left}_{right}"] = (
                    frame[left].astype("string").fillna("None")
                    + "_"
                    + frame[right].astype("string").fillna("None")
                )
        if "YrSold" in frame:
            sale_year = numeric("YrSold")
            frame["SaleYear"] = sale_year
            frame["SaleMonthSin"] = np.sin(2.0 * np.pi * numeric("MoSold") / 12.0)
            frame["SaleMonthCos"] = np.cos(2.0 * np.pi * numeric("MoSold") / 12.0)

        # 对右偏非负变量加入 log1p 视图；只在线性模型分支启用，避免树模型增加冗余切分。
        if self.include_linear_views:
            log_features = {
                f"Log1p_{column}": np.log1p(np.clip(numeric(column), 0.0, None))
                for column in self._log_columns_
                if column in frame
            }
            if log_features:
                frame = pd.concat([frame, pd.DataFrame(log_features, index=frame.index)], axis=1)

        # 质量×面积、质量×车库和年龄交互，增强线性模型表达能力。
        quality = numeric("OverallQual")
        condition = numeric("OverallCond")
        frame["OverallQualSq"] = np.square(quality)
        frame["OverallCondSq"] = np.square(condition)
        frame["Qual_GrLivArea"] = quality * np.log1p(np.clip(numeric("GrLivArea"), 0.0, None))
        frame["Qual_TotalSF"] = quality * np.log1p(np.clip(numeric("TotalSF"), 0.0, None))
        frame["Qual_GarageCars"] = quality * numeric("GarageCars")
        frame["Qual_Age"] = quality * numeric("AgeAtSale")
        rooms = numeric("TotRmsAbvGrd").clip(lower=1)
        frame["GrLivAreaPerRoom"] = numeric("GrLivArea") / rooms
        frame["TotalSFPerRoom"] = numeric("TotalSF") / rooms
        # 一次性整理列块，避免大量逐列插入造成训练开销和碎片化警告。
        return frame.copy()


def _preprocessor(
    reference: pd.DataFrame,
    *,
    scale_numeric: bool,
    include_linear_views: bool,
) -> ColumnTransformer:
    engineered = HouseFeatureEngineer(include_linear_views=include_linear_views).fit_transform(reference)
    categorical = engineered.select_dtypes(include=["object", "string", "category"]).columns.tolist()
    numeric = [column for column in engineered.columns if column not in categorical]
    # 缺失指示器保留“原始缺失”信息；结构性缺失已在特征工程阶段转为 None/0。
    numeric_steps: list[tuple[str, Any]] = [("imputer", SimpleImputer(strategy="median", add_indicator=True))]
    if scale_numeric:
        numeric_steps.append(("scaler", RobustScaler()))
    numeric_pipeline = Pipeline(numeric_steps)
    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="constant", fill_value="__MISSING__")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    return ColumnTransformer(
        [("numeric", numeric_pipeline, numeric), ("categorical", categorical_pipeline, categorical)],
        remainder="drop",
        sparse_threshold=0.0,
    )


def build_estimator(
    family: str,
    parameters: dict[str, Any],
    reference: pd.DataFrame,
    *,
    seed: int,
    n_jobs: int,
) -> Pipeline:
    """构造包含同一特征工程和折内拟合预处理器的回归管道。"""

    native_categorical = family == "catboost_native"
    scale_numeric = family in {"elastic_net", "lasso", "ridge", "kernel_ridge", "svr"}
    include_linear_views = scale_numeric
    preprocess = None if native_categorical else _preprocessor(
        reference,
        scale_numeric=scale_numeric,
        include_linear_views=include_linear_views,
    )
    if family == "elastic_net":
        estimator: Any = ElasticNet(random_state=seed, **parameters)
    elif family == "lasso":
        estimator = Lasso(random_state=seed, **parameters)
    elif family == "ridge":
        estimator = Ridge(**parameters)
    elif family == "kernel_ridge":
        estimator = KernelRidge(**parameters)
    elif family == "svr":
        estimator = SVR(**parameters)
    elif family == "gradient_boosting":
        estimator = GradientBoostingRegressor(random_state=seed, **parameters)
    elif family == "xgboost":
        from xgboost import XGBRegressor

        estimator = XGBRegressor(
            objective="reg:squarederror",
            eval_metric="rmse",
            random_state=seed,
            n_jobs=n_jobs,
            tree_method="hist",
            **parameters,
        )
    elif family == "lightgbm":
        from lightgbm import LGBMRegressor

        estimator = LGBMRegressor(
            objective="regression",
            random_state=seed,
            n_jobs=n_jobs,
            verbosity=-1,
            subsample_freq=1 if float(parameters.get("subsample", 1.0)) < 0.999 else 0,
            **parameters,
        )
    elif family in {"catboost", "catboost_native"}:
        adapter = CatBoostRawAdapter if native_categorical else CatBoostSklearnAdapter
        catboost_parameters = {
            "loss_function": "RMSE",
            "random_seed": seed,
            "thread_count": n_jobs,
            "verbose": False,
            "allow_writing_files": False,
            **parameters,
        }
        estimator = adapter(**catboost_parameters)
    else:
        raise ValueError(f"不支持的模型族: {family}")
    steps: list[tuple[str, Any]] = [("features", HouseFeatureEngineer(include_linear_views=include_linear_views))]
    if preprocess is not None:
        steps.append(("preprocess", preprocess))
    steps.append(("regressor", estimator))
    return Pipeline(steps)


def optimize_blend_weights(
    prediction_matrix: np.ndarray,
    target_log: np.ndarray,
    l2: float,
) -> np.ndarray:
    """在开发集 OOF 预测上求非负且和为一的稳健权重。"""

    matrix = np.asarray(prediction_matrix, dtype="float64")
    target = np.asarray(target_log, dtype="float64")
    if matrix.ndim != 2 or matrix.shape[1] < 1:
        raise ValueError("融合矩阵必须为非空二维矩阵")
    family_count = matrix.shape[1]
    equal = np.full(family_count, 1.0 / family_count)

    if matrix.ndim != 2 or matrix.shape[0] != target.shape[0] or matrix.shape[1] < 1:
        raise ValueError("融合矩阵与目标形状不一致")
    if not np.isfinite(matrix).all() or not np.isfinite(target).all():
        raise ValueError("融合输入包含非有限值")
    if not np.isfinite(l2) or l2 < 0.0:
        raise ValueError("融合 L2 必须为非负有限值")

    def objective(weights: np.ndarray) -> float:
        residual = matrix @ weights - target
        return float(np.mean(np.square(residual)) + l2 * np.sum(np.square(weights - equal)))

    result = minimize(
        objective,
        equal,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * family_count,
        constraints={"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)},
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not result.success or not np.isfinite(result.x).all():
        return equal
    weights = np.clip(result.x, 0.0, 1.0)
    return weights / weights.sum()


@dataclass
class HousePriceEnsemble(BaseEstimator, RegressorMixin):
    """持久化多个完整管道，并输出非负 SalePrice。"""

    families: tuple[str, ...]
    estimators: tuple[Any, ...]
    weights: tuple[float, ...]
    preprocessing_version: str

    def fit(self, X: Any, y: Any = None) -> "HousePriceEnsemble":
        return self

    def predict_log(self, X: pd.DataFrame) -> np.ndarray:
        # 模型对象自身也负责去除 Id/目标列，并与提交路径保持逐模型裁剪语义一致。
        frame = X.drop(columns=["Id", "SalePrice"], errors="ignore")
        matrix = np.column_stack(
            [np.maximum(np.asarray(estimator.predict(frame), dtype="float64"), 0.0) for estimator in self.estimators]
        )
        return np.maximum(matrix @ np.asarray(self.weights, dtype="float64"), 0.0)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.clip(np.expm1(self.predict_log(X)), 0.0, None)
