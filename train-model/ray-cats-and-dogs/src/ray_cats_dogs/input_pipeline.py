"""TensorFlow input pipelines backed by Ray Dataset shards or local manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _decode_one(path: Any, label: Any, image_size: tuple[int, int]) -> tuple[Any, Any]:
    import tensorflow as tf

    encoded = tf.io.read_file(path)
    image = tf.io.decode_image(encoded, channels=3, expand_animations=False)
    image.set_shape((None, None, 3))
    image = tf.image.resize(image, image_size, antialias=True)
    return tf.cast(image, tf.float32), tf.cast(label, tf.int64)


def make_worker_dataset(
    dataset_shard: Any,
    *,
    image_size: tuple[int, int],
    batch_size: int,
    training: bool,
    seed: int,
) -> Any:
    import tensorflow as tf
    from ray.train.tensorflow import prepare_dataset_shard

    shuffle_buffer = max(batch_size * 8, batch_size) if training else None
    dataset = dataset_shard.to_tf(
        feature_columns="path",
        label_columns="label",
        batch_size=batch_size,
        drop_last=False,
        local_shuffle_buffer_size=shuffle_buffer,
        local_shuffle_seed=seed if training else None,
        feature_type_spec=tf.TensorSpec(shape=(None,), dtype=tf.string, name="path"),
        label_type_spec=tf.TensorSpec(shape=(None,), dtype=tf.int64, name="label"),
    )
    dataset = prepare_dataset_shard(dataset)

    def decode_batch(paths: Any, labels: Any) -> tuple[Any, Any]:
        images = tf.map_fn(
            lambda path: _decode_one(path, tf.constant(0), image_size)[0],
            paths,
            fn_output_signature=tf.TensorSpec((*image_size, 3), tf.float32),
        )
        return images, labels

    return dataset.map(decode_batch, num_parallel_calls=tf.data.AUTOTUNE).prefetch(
        tf.data.AUTOTUNE
    )


def make_local_dataset(
    frame: pd.DataFrame,
    pet_images_root: Path,
    *,
    image_size: tuple[int, int],
    batch_size: int,
) -> Any:
    import tensorflow as tf

    paths = [str(pet_images_root / relative) for relative in frame["relative_path"]]
    labels = frame["label"].astype("int64").tolist()
    dataset = tf.data.Dataset.from_tensor_slices((paths, labels))
    dataset = dataset.map(
        lambda path, label: _decode_one(path, label, image_size),
        num_parallel_calls=tf.data.AUTOTUNE,
        deterministic=True,
    )
    return dataset.batch(batch_size).prefetch(tf.data.AUTOTUNE)
