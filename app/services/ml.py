from functools import lru_cache

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

FEATURES = ("files", "funcs", "complexity", "cycles", "doc_before", "lang_py")


def make_dataset(seed: int = 42, samples: int = 1000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    files = rng.integers(1, 501, samples)
    funcs = files * rng.integers(2, 11, samples)
    complexity = rng.uniform(1, 10, samples)
    cycles = rng.integers(0, 51, samples)
    doc_before = rng.uniform(0, 100, samples)
    lang_py = rng.integers(0, 2, samples)
    x = np.column_stack((files, funcs, complexity, cycles, doc_before, lang_py))
    y = (
        0.5
        + funcs * 0.05
        + complexity * 0.8
        + cycles * 0.1
        - doc_before * 0.01
        - lang_py * 0.12
        + rng.normal(0, 2, samples)
    )
    return x, y


@lru_cache(maxsize=1)
def trained_model() -> tuple[LinearRegression, dict[str, float]]:
    x, y = make_dataset()
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, random_state=42, shuffle=True
    )
    model = LinearRegression().fit(x_train, y_train)
    predicted = model.predict(x_test)
    metrics = {
        "r2": float(r2_score(y_test, predicted)),
        "mae_seconds": float(mean_absolute_error(y_test, predicted)),
        "rmse_seconds": float(np.sqrt(mean_squared_error(y_test, predicted))),
    }
    return model, metrics


def predict(features: list[float]) -> dict:
    model, metrics = trained_model()
    predicted_time = max(0.0, float(model.predict([features])[0]))
    importance = dict(zip(FEATURES, map(float, model.coef_), strict=True))
    recommendation = (
        "Split the project into smaller modules." if predicted_time > 60 else "Generation should finish within 60 seconds."
    )
    return {
        "predicted_time_sec": round(predicted_time, 2),
        "recommendation": recommendation,
        "confidence": round(max(0.0, min(1.0, metrics["r2"])), 2),
        "model_metrics": metrics,
        "feature_importance": importance,
    }


@lru_cache(maxsize=1)
def model_plot_points() -> list[dict[str, float]]:
    x, y = make_dataset()
    _, x_test, _, y_test = train_test_split(x, y, test_size=0.2, random_state=42, shuffle=True)
    model, _ = trained_model()
    predictions = model.predict(x_test)
    stride = max(1, len(y_test) // 50)
    return [
        {"actual": round(float(actual), 2), "predicted": round(float(predicted), 2)}
        for actual, predicted in zip(y_test[::stride], predictions[::stride], strict=True)
    ]
