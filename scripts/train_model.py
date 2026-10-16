from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.services.ml import FEATURES, make_dataset, trained_model


def main() -> None:
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
    x, y = make_dataset()
    dataset = pd.DataFrame(x, columns=FEATURES)
    dataset["time_gen_sec"] = y
    dataset.to_csv(output / "generation_metrics.csv", index=False)
    model, metrics = trained_model()
    joblib.dump(model, output / "model.joblib")
    np.set_printoptions(precision=3, suppress=True)
    print(f"Saved {len(y)} synthetic samples and model; held-out metrics: {metrics}")


if __name__ == "__main__":
    main()
