"""TensorFlow model families for binary cat and dog classification."""

from __future__ import annotations

from typing import Any


def build_model(
    model_config: dict[str, Any],
    training_config: dict[str, Any],
    image_size: tuple[int, int],
    seed: int,
) -> Any:
    import tensorflow as tf

    tf.keras.utils.set_random_seed(seed)
    inputs = tf.keras.layers.Input(shape=(*image_size, 3), name="image")
    x = inputs
    if model_config["augmentation"]:
        x = tf.keras.Sequential(
            [
                tf.keras.layers.RandomFlip("horizontal", seed=seed),
                tf.keras.layers.RandomRotation(0.08, seed=seed + 1),
                tf.keras.layers.RandomTranslation(0.10, 0.10, seed=seed + 2),
            ],
            name="augmentation",
        )(x)

    family = model_config["family"]
    if family == "custom_cnn":
        x = tf.keras.layers.Rescaling(1.0 / 255, name="rescale")(x)
        for filters in (32, 64, 128):
            x = tf.keras.layers.Conv2D(filters, 3, padding="same", activation="relu")(x)
            x = tf.keras.layers.MaxPooling2D(2)(x)
        x = tf.keras.layers.Conv2D(256, 3, padding="same", activation="relu")(x)
    elif family == "mobilenet_v2":
        x = tf.keras.applications.mobilenet_v2.preprocess_input(x)
        backbone = tf.keras.applications.MobileNetV2(
            include_top=False,
            weights=model_config["pretrained_weights"],
            input_shape=(*image_size, 3),
        )
        backbone.trainable = False
        x = backbone(x, training=False)
    else:
        raise ValueError(f"Unsupported model family: {family}")

    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    if model_config["dropout"]:
        x = tf.keras.layers.Dropout(model_config["dropout"], seed=seed + 3)(x)
    x = tf.keras.layers.Dense(model_config["dense_units"], activation="relu")(x)
    outputs = tf.keras.layers.Dense(2, activation="softmax", name="class_probability")(x)
    model = tf.keras.Model(inputs, outputs, name=f"cats_dogs_{family}")
    optimizer_class = {
        "adam": tf.keras.optimizers.Adam,
        "rmsprop": tf.keras.optimizers.RMSprop,
    }[training_config["optimizer"]]
    model.compile(
        optimizer=optimizer_class(learning_rate=training_config["learning_rate"]),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model
