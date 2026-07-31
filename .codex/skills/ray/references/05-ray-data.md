# Ray Data - Datasets, Transformations, DataSources, and Preprocessors

## Overview

Ray Data is a distributed data processing library built on top of Ray Core. It provides a high-level API for loading, transforming, and writing data in a distributed fashion. Ray Data uses a block-based execution model where datasets are partitioned into blocks that can be processed in parallel across the cluster.

## Dataset Creation

### from_items

Create a dataset from a list of Python items.

```python
import ray.data as rd

# Simple items
ds = rd.from_items([1, 2, 3, 4, 5])
# Each item becomes a row: {"item": 1}, {"item": 2}, ...

# Dictionary items
ds = rd.from_items([
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
])

# With parallelism override
ds = rd.from_items(list(range(1000)), parallelism=10)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `items` | List[Any] | required | List of Python objects |
| `parallelism` | int | -1 | Number of blocks to create |

### from_pandas

Create a dataset from one or more pandas DataFrames.

```python
import pandas as pd
import ray.data as rd

# Single DataFrame
df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
ds = rd.from_pandas(df)

# Multiple DataFrames (creates multiple blocks)
dfs = [pd.DataFrame({"a": [i]}) for i in range(10)]
ds = rd.from_pandas(dfs)
```

### from_numpy

Create a dataset from one or more numpy arrays.

```python
import numpy as np
import ray.data as rd

# Single array
arr = np.array([1, 2, 3, 4, 5])
ds = rd.from_numpy(arr)

# Multiple arrays
arrays = [np.array([i]) for i in range(10)]
ds = rd.from_numpy(arrays)
```

### from_arrow

Create a dataset from one or more Arrow tables.

```python
import pyarrow as pa
import ray.data as rd

# Single Arrow table
table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
ds = rd.from_arrow(table)

# Multiple Arrow tables
tables = [pa.table({"a": [i]}) for i in range(10)]
ds = rd.from_arrow(tables)
```

### read_parquet

Read Parquet files into a dataset.

```python
import ray.data as rd

# From local path
ds = rd.read_parquet("/path/to/data/")

# From S3
ds = rd.read_parquet("s3://bucket/data/")

# With columns selection
ds = rd.read_parquet("s3://bucket/data/", columns=["col1", "col2"])

# With row filter (pushdown)
ds = rd.read_parquet("s3://bucket/data/",
    filter=pa.dataset.field("age") > 18)

# With parallelism
ds = rd.read_parquet("s3://bucket/data/", parallelism=100)

# With filesystem override
ds = rd.read_parquet("s3://bucket/data/",
    filesystem=pa.fs.S3FileSystem(region="us-west-2"))

# Arrow-specific options
ds = rd.read_parquet("path/",
    arrow_open_file_args={"memory_map": True},
    tensor_column_casting="default")
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `paths` | str/List[str] | required | File path(s) or glob pattern |
| `filesystem` | pyarrow.fs.FileSystem | None | Filesystem to use |
| `columns` | List[str] | None | Columns to read |
| `filter` | Expression | None | Row filter expression |
| `parallelism` | int | -1 | Desired parallelism |
| `ray_remote_args` | dict | None | Remote function args |
| `tensor_column_casting` | str | None | Tensor casting behavior |
| `meta_provider` | BaseFileMetadataProvider | None | Metadata provider |
| `partition_filter` | PathPartitionFilter | None | Partition filter |
| `shuffle` | str | None | "files" to shuffle file ordering |

### read_csv

Read CSV files into a dataset.

```python
import ray.data as rd

ds = rd.read_csv("s3://bucket/data.csv")
ds = rd.read_csv("/path/to/*.csv")

# With read options
ds = rd.read_csv("data/",
    read_options=pa.csv.ReadOptions(
        column_names=["a", "b", "c"],
        skip_rows=1,
    ),
    parse_options=pa.csv.ParseOptions(
        delimiter=",",
        quote_char='"',
    ))
```

### read_json

Read JSON files into a dataset.

```python
import ray.data as rd

# JSON lines format (default)
ds = rd.read_json("s3://bucket/data.jsonl")

# Directory of JSON files
ds = rd.read_json("/path/to/json_dir/")

# With Arrow parse options
import pyarrow.json as paj
ds = rd.read_json("data/",
    parse_options=paj.ParseOptions(
        explicit_schema=None,
        newlines_in_values=False,
    ))
```

### read_text

Read text files into a dataset (one row per line).

```python
import ray.data as rd

ds = rd.read_text("s3://bucket/logs/")
ds = rd.read_text("/path/to/*.txt")

# With parser (drop empty lines)
ds = rd.read_text("data.txt", drop_empty_lines=True)
```

### read_images

Read image files into a dataset.

```python
import ray.data as rd

# Read images as numpy arrays
ds = rd.read_images("s3://bucket/images/", size=(224, 224))  # Resize

# Read with mode
ds = rd.read_images("/path/to/images/", mode="RGB")  # "RGB", "L", etc.

# Supported formats: PNG, JPEG, TIFF, BMP, GIF (first frame)
```

### read_binary

Read binary files into a dataset.

```python
import ray.data as rd

# Read binary files
ds = rd.read_binary("s3://bucket/binaries/")

# Include paths
ds = rd.read_binary("/path/to/files/", include_paths=True)
# Each row: {"bytes": b"...", "path": "file.bin"}
```

### read_tfrecords

Read TFRecord files into a dataset.

```python
import ray.data as rd

ds = rd.read_tfrecords("s3://bucket/records/")
ds = rd.read_tfrecords("/path/to/*.tfrecord")
```

### read_webdataset

Read WebDataset (tar-based) files into a dataset.

```python
import ray.data as rd

ds = rd.read_webdataset("s3://bucket/wds/")
ds = rd.read_webdataset("/path/to/*.tar")
```

### read_sql

Read from a SQL database into a dataset.

```python
import ray.data as rd

# Basic SQL read
ds = rd.read_sql(
    "SELECT * FROM users WHERE age > 18",
    connection_factory=lambda: psycopg2.connect(...),
)

# With parallelism via partition column
ds = rd.read_sql(
    "SELECT * FROM large_table",
    connection_factory=lambda: create_engine(...).connect(),
    partition_column="id",
    num_partitions=100,
)
```

### read_numpy

Read numpy files into a dataset.

```python
import ray.data as rd

ds = rd.read_numpy("s3://bucket/arrays/")
ds = rd.read_numpy("/path/to/*.npy")
```

### range and range_tensor

Create synthetic datasets.

```python
import ray.data as rd

# Integer range
ds = rd.range(1000)
# Each row: {"id": 0}, {"id": 1}, ...

# With parallelism
ds = rd.range(1000, parallelism=10)

# Tensor range (for ML workloads)
ds = rd.range_tensor(1000, shape=(3, 224, 224), dtype=np.float32)
```

**Parameters for range:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | required | Upper bound (exclusive) |
| `parallelism` | int | -1 | Number of blocks |

**Parameters for range_tensor:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | required | Number of rows |
| `shape` | Tuple | required | Shape of each tensor |
| `dtype` | numpy.dtype | float64 | Data type |
| `parallelism` | int | -1 | Number of blocks |

## Transformations

### map

Apply a function to each row.

```python
# Row-based mapping
ds = ds.map(lambda row: {"x": row["a"] * 2, "y": row["b"] + 1})

# With compute strategy
from ray.data import ActorPoolStrategy
ds = ds.map(
    lambda row: process(row),
    compute=ActorPoolStrategy(size=4),
)

# With resource requests
ds = ds.map(
    lambda row: gpu_process(row),
    num_gpus=0.5,
    num_cpus=2,
)

# With runtime env
ds = ds.map(
    lambda row: nlp_process(row),
    runtime_env={"pip": ["transformers"]},
)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fn` | Callable | required | Transform function |
| `fn_args` | tuple | None | Positional args for fn |
| `fn_kwargs` | dict | None | Keyword args for fn |
| `compute` | str/ComputeStrategy | None | "tasks" or ActorPoolStrategy |
| `num_cpus` | float | None | CPUs per map task |
| `num_gpus` | float | None | GPUs per map task |
| `memory` | float | None | Memory per map task |
| `resources` | dict | None | Custom resources |
| `concurrency` | int | None | Max concurrent tasks |
| `runtime_env` | dict | None | Runtime environment |

### map_batches

Apply a function to batches of rows. The most efficient transformation for most use cases.

```python
# Pandas-based batch processing
ds = ds.map_batches(
    lambda df: df.assign(x=df["a"] * 2),
    batch_size=256,
    batch_format="pandas",
)

# NumPy-based batch processing
ds = ds.map_batches(
    lambda arr: process_array(arr),
    batch_format="numpy",
)

# Arrow-based batch processing
ds = ds.map_batches(
    lambda table: process_table(table),
    batch_format="arrow",
)

# Default (row-based dict)
ds = ds.map_batches(
    lambda batch: [{"x": row["a"] * 2} for row in batch],
    batch_size=1024,
)

# Zero-copy batch (for GPU workloads)
ds = ds.map_batches(
    gpu_transform,
    batch_size=4096,
    zero_copy_batch=True,
    num_gpus=1,
    compute=ActorPoolStrategy(size=2),
)

# Stateful batch transform with actor pool
class BatchProcessor:
    def __init__(self):
        self.model = load_model()

    def __call__(self, batch):
        return self.model.predict(batch)

ds = ds.map_batches(
    BatchProcessor,
    batch_size=512,
    compute=ActorPoolStrategy(size=4),
    num_gpus=1,
)
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fn` | Callable/class | required | Batch transform function or class |
| `batch_size` | int | None | Desired batch size (default: block size) |
| `batch_format` | str | "default" | "default", "pandas", "numpy", "arrow" |
| `zero_copy_batch` | bool | False | Zero-copy batch transfer |
| `compute` | str/ComputeStrategy | None | Compute strategy |
| `num_cpus` | float | None | CPUs per task |
| `num_gpus` | float | None | GPUs per task |
| `memory` | float | None | Memory per task |
| `resources` | dict | None | Custom resources |
| `concurrency` | int | None | Max concurrent tasks |
| `fn_args` | tuple | None | Positional args |
| `fn_kwargs` | dict | None | Keyword args |
| `runtime_env` | dict | None | Runtime environment |

### flat_map

Apply a function that returns multiple rows per input row.

```python
# Flatten nested data
ds = ds.flat_map(
    lambda row: [{"word": w} for w in row["sentence"].split()]
)

# One-to-many expansion
ds = ds.flat_map(
    lambda row: [{"val": row["x"] + i} for i in range(3)]
)
```

### filter

Filter rows based on a predicate.

```python
# Simple filter
ds = ds.filter(lambda row: row["age"] > 18)

# Complex filter
ds = ds.filter(
    lambda row: row["status"] == "active" and row["score"] > 0.5
)
```

### select_columns

Select a subset of columns.

```python
# Select specific columns
ds = ds.select_columns(["name", "age", "score"])

# Select single column
ds = ds.select_columns("id")
```

### drop_columns

Remove columns from the dataset.

```python
# Drop specific columns
ds = ds.drop_columns(["internal_id", "debug_flag"])

# Drop single column
ds = ds.drop_columns("temp_column")
```

### add_column

Add a new column computed from existing rows.

```python
# Add column from row function
ds = ds.add_column(
    "full_name",
    lambda row: f"{row['first']} {row['last']}"
)

# Add constant column
ds = ds.add_column("version", lambda row: 2)
```

### repartition

Change the number of blocks in the dataset.

```python
# Increase parallelism
ds = ds.repartition(100)

# Decrease parallelism
ds = ds.repartition(4)

# Repartition with target block size
ds = ds.repartition(target_max_block_size=128 * 1024 * 1024)
```

### random_shuffle

Randomly shuffle the rows of the dataset.

```python
# Simple shuffle
ds = ds.random_shuffle()

# With seed for reproducibility
ds = ds.random_shuffle(seed=42)

# With custom number of output blocks
ds = ds.random_shuffle(num_outputs=50)
```

### sort

Sort the dataset by one or more columns.

```python
# Single column sort
ds = ds.sort("score")

# Descending sort
ds = ds.sort("score", descending=True)

# Multi-column sort
ds = ds.sort(["age", "name"])

# With key function
ds = ds.sort(key=lambda row: row["score"], descending=True)
```

### groupby

Group rows and apply aggregations.

```python
# Single aggregation
result = ds.groupby("category").mean("price")

# Multiple aggregations
result = ds.groupby("category").aggregate(
    {"price": ["mean", "std", "count"]}
)

# Custom aggregation function
result = ds.groupby("region").aggregate(
    lambda g: {"total": g["amount"].sum()}
)

# Groupby with map_groups
result = ds.groupby("key").map_groups(
    lambda group: process_group(group),
    batch_format="pandas",
)
```

### aggregate

Perform aggregation operations on the dataset.

```python
# Built-in aggregations
total = ds.sum("column")
avg = ds.mean("column")
min_val = ds.min("column")
max_val = ds.max("column")
count = ds.count()

# Aggregate to single value
result = ds.aggregate(
    lambda blocks: sum(block["value"].sum() for block in blocks)
)
```

### join

Join two datasets (distributed hash join).

```python
# Inner join (default)
result = ds.join(ds2, on="key", how="inner")

# Left join
result = ds.join(ds2, on="key", how="left")

# Right join
result = ds.join(ds2, on="key", how="right")

# Outer join
result = ds.join(ds2, on="key", how="outer")

# Multi-column join
result = ds.join(ds2, on=["key1", "key2"])

# Different column names
result = ds.join(ds2, left_on="id", right_on="user_id", how="inner")
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `other` | Dataset | required | Right dataset |
| `on` | str/List[str] | required | Join column(s) |
| `how` | str | "inner" | "inner", "left", "right", "outer" |
| `left_on` | str/List[str] | None | Left join columns |
| `right_on` | str/List[str] | None | Right join columns |
| `suffixes` | tuple | None | Suffix for duplicate columns |
| `num_partitions` | int | None | Partitions for join |

### union

Combine multiple datasets.

```python
# Union two datasets
combined = ds.union(ds2)

# Union multiple datasets
combined = ds.union([ds2, ds3, ds4])
```

### split

Split dataset into multiple parts.

```python
# Split into N equal parts
parts = ds.split(n=3, equal=True)

# Unequal split
parts = ds.split(n=2)  # Approximate split

# Split at specific indices
parts = ds.split_at_indices([100, 200])
# Returns [0:100], [100:200], [200:]
```

### take

Take N rows from the dataset.

```python
# Take first N rows
rows = ds.take(5)  # Returns list of dicts

# Take all rows (materializes entire dataset)
rows = ds.take_all()
```

### show

Print the first N rows.

```python
# Display rows
ds.show(5)

# Display with limit
ds.show(limit=10)
```

### count

Count the number of rows.

```python
n = ds.count()
```

### materialize

Force execution of all pending transformations.

```python
# Materialize (execute) all lazy transformations
ds = ds.materialize()

# Useful for caching intermediate results
ds = ds.map(expensive_fn).materialize()
```

### write_parquet

Write dataset to Parquet files.

```python
ds.write_parquet("s3://bucket/output/")
ds.write_parquet("/local/path/output/")

# With compression
ds.write_parquet("output/", compression="snappy")

# With partitioning
ds.write_parquet("output/", partition_cols=["year", "month"])

# With row group size
ds.write_parquet("output/", min_rows_per_file=1000000)
```

### write_csv

Write dataset to CSV files.

```python
ds.write_csv("s3://bucket/output/")
ds.write_csv("/local/output/")
```

### write_json

Write dataset to JSON files.

```python
ds.write_json("s3://bucket/output/")
ds.write_json("/local/output/")

# With pandas orientation
ds.write_json("output/", pandas_json_kwargs={"orient": "records"})
```

### write_tfrecords

Write dataset to TFRecord files.

```python
ds.write_tfrecords("s3://bucket/output/")
ds.write_tfrecords("/local/output/")
```

### write_numpy

Write dataset to numpy files.

```python
ds.write_numpy("s3://bucket/output/")
ds.write_numpy("/local/output/")
```

## DataSources

### Built-in DataSources

Ray Data supports reading from many file formats and data systems through built-in data sources:

| DataSource | Read Function | Write Function | Description |
|-----------|---------------|----------------|-------------|
| CSV | `read_csv` | `write_csv` | Comma-separated values |
| Parquet | `read_parquet` | `write_parquet` | Apache Parquet columnar format |
| JSON | `read_json` | `write_json` | JSON Lines format |
| Text | `read_text` | - | Plain text files |
| Binary | `read_binary` | - | Raw binary files |
| Images | `read_images` | - | Image files (PNG, JPEG, etc.) |
| TFRecords | `read_tfrecords` | `write_tfrecords` | TensorFlow Records |
| WebDataset | `read_webdataset` | - | Tar-based dataset format |
| Numpy | `read_numpy` | `write_numpy` | NumPy array files |
| SQL | `read_sql` | - | SQL databases |
| Delta | `read_delta` | - | Delta Lake tables |
| Iceberg | `read_iceberg` | - | Apache Iceberg tables |
| BigQuery | `read_bigquery` | - | Google BigQuery |
| Snowflake | `read_snowflake` | - | Snowflake data warehouse |
| MongoDB | `read_mongo` | `write_mongo` | MongoDB documents |

### Delta Lake

```python
import ray.data as rd

ds = rd.read_delta("s3://bucket/delta_table")
ds = rd.read_delta("s3://bucket/delta_table",
    version=5,  # Time travel
    delta_version=5,
    delta_timestamp="2024-01-01")
```

### Apache Iceberg

```python
import ray.data as rd

ds = rd.read_iceberg(
    table_name="catalog.db.table",
    catalog_options={
        "type": "rest",
        "uri": "http://localhost:8181",
    },
)
```

### BigQuery

```python
import ray.data as rd

ds = rd.read_bigquery(
    "project.dataset.table",
    filter="age > 18",
)
```

### Snowflake

```python
import ray.data as rd

ds = rd.read_snowflake(
    "SELECT * FROM users",
    connection_parameters={
        "account": "xy12345.us-east-1",
        "user": "admin",
        "password": "secret",
        "database": "mydb",
        "schema": "public",
    },
)
```

### MongoDB

```python
import ray.data as rd

ds = rd.read_mongo(
    uri="mongodb://localhost:27017",
    database="mydb",
    collection="mycollection",
    filter={"status": "active"},
    pipeline=[{"$match": {"score": {"$gt": 0.5}}}],
)

ds.write_mongo(
    uri="mongodb://localhost:27017",
    database="mydb",
    collection="output",
)
```

### Custom DataSource API

Create custom datasources by implementing the `Datasource` interface.

```python
from ray.data import Datasource, ReadTask
from ray.data.block import BlockMetadata

class MyCustomDatasource(Datasource):
    """Custom datasource for reading from a proprietary data store."""

    def prepare_read(self, parallelism, **read_args):
        """Return a list of ReadTasks."""
        # Get metadata about the data
        total_rows = get_total_rows(**read_args)
        rows_per_task = total_rows // parallelism

        read_tasks = []
        for i in range(parallelism):
            start = i * rows_per_task
            end = start + rows_per_task if i < parallelism - 1 else total_rows

            metadata = BlockMetadata(
                num_rows=end - start,
                size_bytes=None,
                input_files=None,
                exec_stats=None,
            )

            read_tasks.append(
                ReadTask(
                    lambda s=start, e=end: read_partition(s, e, **read_args),
                    metadata,
                )
            )

        return read_tasks

    def on_read_complete(self, read_tasks):
        """Called when all read tasks complete."""
        pass

    def do_write(self, blocks, metadata, ray_remote_args, **write_args):
        """Return list of remote write tasks."""
        return [
            write_partition.remote(block, **write_args)
            for block in blocks
        ]

    def on_write_complete(self, write_results):
        """Called when all write tasks complete."""
        pass

    def on_write_failed(self, write_results, error):
        """Called when write fails."""
        pass

# Usage
ds = rd.read_datasource(MyCustomDatasource(), parallelism=10)
ds.write_datasource(MyCustomDatasource())
```

### BlockBasedDatasource

For file-based data sources, extend `FileBasedDatasource`:

```python
from ray.data.datasource import FileBasedDatasource

class MyFileDatasource(FileBasedDatasource):
    _FILE_EXTENSION = "myext"

    def _read_file(self, f, path, **reader_args):
        """Read a single file and return an Arrow table."""
        data = f.readall()
        return parse_my_format(data)

    def _open_input_source(self, filesystem, path, **open_args):
        """Open a file for reading."""
        return filesystem.open_input_file(path)
```

## Preprocessors

Ray Data provides built-in preprocessors for common ML data transformations. Preprocessors follow a `fit` -> `transform` pattern.

### StandardScaler

Standardize features by removing the mean and scaling to unit variance.

```python
from ray.data.preprocessors import StandardScaler

scaler = StandardScaler(columns=["feature1", "feature2"])
scaler.fit(dataset)
transformed = scaler.transform(dataset)

# Stats stored after fit
# scaler.stats_ contains mean and std for each column
```

**Formula:** `z = (x - mean) / std`

### MinMaxScaler

Scale features to a given range (default: [0, 1]).

```python
from ray.data.preprocessors import MinMaxScaler

scaler = MinMaxScaler(columns=["feature1", "feature2"])
# Default range: [0, 1]
scaler.fit(dataset)
transformed = scaler.transform(dataset)

# Custom range
scaler = MinMaxScaler(columns=["feature1"], clip=True)
```

**Formula:** `z = (x - min) / (max - min)`

### MaxAbsScaler

Scale features by their maximum absolute value.

```python
from ray.data.preprocessors import MaxAbsScaler

scaler = MaxAbsScaler(columns=["feature1", "feature2"])
scaler.fit(dataset)
transformed = scaler.transform(dataset)
```

**Formula:** `z = x / max(|x|)`

### RobustScaler

Scale features using statistics that are robust to outliers.

```python
from ray.data.preprocessors import RobustScaler

scaler = RobustScaler(
    columns=["feature1", "feature2"],
    quantile_range=(25.0, 75.0),  # IQR range
)
scaler.fit(dataset)
transformed = scaler.transform(dataset)
```

**Formula:** `z = (x - median) / IQR`

### OneHotEncoder

Encode categorical features as one-hot numeric arrays.

```python
from ray.data.preprocessors import OneHotEncoder

encoder = OneHotEncoder(columns=["color", "size"])
encoder.fit(dataset)
transformed = encoder.transform(dataset)
# Creates columns: "color_red", "color_blue", "size_S", "size_M", etc.

# With max categories
encoder = OneHotEncoder(columns=["feature"], max_categories={"feature": 100})
```

### LabelEncoder

Encode string labels as integers.

```python
from ray.data.preprocessors import LabelEncoder

encoder = LabelEncoder(columns=["category"])
encoder.fit(dataset)
transformed = encoder.transform(dataset)
# "cat" -> 0, "dog" -> 1, "bird" -> 2, etc.
```

### OrdinalEncoder

Encode categorical features as ordinal integers.

```python
from ray.data.preprocessors import OrdinalEncoder

# With explicit ordering
encoder = OrdinalEncoder(
    columns=["size"],
    encode={{"size": ["S", "M", "L", "XL"]}}
)
encoder.fit(dataset)
transformed = encoder.transform(dataset)
```

### SimpleImputer

Impute missing values using a strategy.

```python
from ray.data.preprocessors import SimpleImputer

# Mean imputation (default)
imputer = SimpleImputer(columns=["feature1", "feature2"])

# Median imputation
imputer = SimpleImputer(columns=["feature1"], strategy="median")

# Constant fill
imputer = SimpleImputer(columns=["feature1"], strategy="constant", fill_value=0)

# Most frequent
imputer = SimpleImputer(columns=["feature1"], strategy="most_frequent")

imputer.fit(dataset)
transformed = imputer.transform(dataset)
```

**Strategies:** `mean`, `median`, `most_frequent`, `constant`

### Tokenizer

Tokenize text columns into word tokens.

```python
from ray.data.preprocessors import Tokenizer

tokenizer = Tokenizer(columns=["text"])
transformed = tokenizer.transform(dataset)
# "hello world" -> ["hello", "world"]
```

### HashingVectorizer

Vectorize text using the hashing trick.

```python
from ray.data.preprocessors import HashingVectorizer

vectorizer = HashingVectorizer(
    columns=["text"],
    tokenization_fn=str.split,
    output_dim=2**18,  # Hash table size
)
vectorizer.fit(dataset)
transformed = vectorizer.transform(dataset)
```

### CountVectorizer

Vectorize text using term counts.

```python
from ray.data.preprocessors import CountVectorizer

vectorizer = CountVectorizer(
    columns=["text"],
    tokenization_fn=str.split,
    max_features=10000,
    min_df=2,
    max_df=0.95,
)
vectorizer.fit(dataset)
transformed = vectorizer.transform(dataset)
```

### PowerTransformer

Apply power transform to make data more Gaussian-like.

```python
from ray.data.preprocessors import PowerTransformer

transformer = PowerTransformer(
    columns=["feature1", "feature2"],
    method="box-cox",  # or "yeo-johnson"
)
transformer.fit(dataset)
transformed = transformer.transform(dataset)
```

### Chain

Chain multiple preprocessors together.

```python
from ray.data.preprocessors import (
    Chain, SimpleImputer, StandardScaler, OneHotEncoder
)

pipeline = Chain(
    SimpleImputer(columns=["numeric1", "numeric2"]),
    StandardScaler(columns=["numeric1", "numeric2"]),
    OneHotEncoder(columns=["category"]),
)
pipeline.fit(dataset)
transformed = pipeline.transform(dataset)
```

### BatchMapper

Apply a custom batch-level transformation as a preprocessor.

```python
from ray.data.preprocessors import BatchMapper

def my_transform(df):
    df["new_col"] = df["a"] * df["b"]
    return df

mapper = BatchMapper(
    fn=my_transform,
    batch_format="pandas",
    batch_size=1024,
)
transformed = mapper.transform(dataset)
```

### Preprocessor Transform Pipeline

```python
# Full ML preprocessing pipeline
from ray.data.preprocessors import Chain, SimpleImputer, StandardScaler

# Step 1: Fit preprocessor on training data
preprocessor = Chain(
    SimpleImputer(columns=["feature1", "feature2"]),
    StandardScaler(columns=["feature1", "feature2"]),
)
preprocessor.fit(train_dataset)

# Step 2: Transform training data
train_transformed = preprocessor.transform(train_dataset)

# Step 3: Transform test data (using same fitted stats)
test_transformed = preprocessor.transform(test_dataset)

# Step 4: Use in Ray Train
from ray.train import ScalingConfig
from ray.train.torch import TorchTrainer

trainer = TorchTrainer(
    train_loop_per_worker=train_func,
    datasets={"train": train_transformed},
    preprocessor=preprocessor,  # Will transform on-the-fly
    scaling_config=ScalingConfig(num_workers=4),
)
```

## DataContext

DataContext controls the global behavior of Ray Data operations.

```python
from ray.data import DataContext

ctx = DataContext.get_current()

# Configuration options
ctx.target_max_block_size = 128 * 1024 * 1024  # 128MB default
ctx.shuffle_strategy = None  # None, "sort", "pandas"
ctx.use_polars_sort = False  # Use Polars for sorting (experimental)
ctx.actor_task_retry_on_failure = True
ctx.enable_get_object_locations_for_tasks = False
ctx.optimization_level = 1  # 0: none, 1: basic, 2: aggressive

# Create with custom config
ctx = DataContext(
    target_max_block_size=256 * 1024 * 1024,  # 256MB blocks
    use_polars_sort=True,
)
DataContext.set_current(ctx)
```

### DataContext Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_max_block_size` | int | 128MB | Target maximum block size in bytes |
| `shuffle_strategy` | str | None | Shuffle strategy ("sort", "pandas") |
| `use_polars_sort` | bool | False | Use Polars for sort operations |
| `actor_task_retry_on_failure` | bool | True | Retry failed actor tasks |
| `enable_get_object_locations_for_tasks` | bool | False | Optimize task placement |
| `optimization_level` | int | 1 | Query optimization level (0-2) |
| `pipeline_pushdown` | bool | False | Push down pipeline stages |
| `optimizer_enabled` | bool | True | Enable query optimizer |
| `use_ray_tqdm` | bool | True | Use tqdm progress bars |
| `print_on_execution_start` | bool | True | Print execution plan |
| `trace_allocation` | bool | False | Trace block allocation |
| `enable_pandas_block` | bool | True | Enable Pandas block format |
| `eager_free` | bool | False | Eagerly free memory |
| `max_errored_blocks` | int | 0 | Max blocks allowed to error |

## Compute Strategies

### TaskPoolStrategy

Execute transformations using Ray tasks (stateless workers).

```python
from ray.data import TaskPoolStrategy

# Default: use tasks
ds = ds.map_batches(
    process_fn,
    compute="tasks",  # or TaskPoolStrategy()
)

# Size controls how many tasks run concurrently
ds = ds.map_batches(
    process_fn,
    compute=TaskPoolStrategy(size=8),
)
```

### ActorPoolStrategy

Execute transformations using Ray actors (stateful workers). Best for expensive initialization (e.g., loading models).

```python
from ray.data import ActorPoolStrategy

# Fixed pool size
ds = ds.map_batches(
    ModelInference,
    compute=ActorPoolStrategy(size=4),
    num_gpus=1,
    batch_size=512,
)

# Min/max pool size (autoscaling actor pool)
ds = ds.map_batches(
    HeavyProcessor,
    compute=ActorPoolStrategy(min_size=2, max_size=8),
    batch_size=256,
)

# Class-based actor (must implement __call__)
class ModelInference:
    def __init__(self):
        self.model = load_heavy_model()

    def __call__(self, batch):
        return self.model.predict(batch)
```

**ActorPoolStrategy Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_size` | int | 0 | Minimum number of actors |
| `max_size` | int | None | Maximum number of actors |
| `size` | int | None | Fixed pool size (sets min=max) |

## Streaming Execution

Ray Data uses a streaming execution model that processes data incrementally.

### Iterating Over Results

```python
# Iterate row by row (streaming)
for row in ds.iter_rows():
    process(row)

# Iterate in batches
for batch in ds.iter_batches(batch_size=256, batch_format="pandas"):
    process(batch)

# Iterate over Arrow batches
for batch in ds.iter_batches(batch_format="arrow", batch_size=1024):
    process(batch)

# With prefetch
for batch in ds.iter_batches(
    batch_size=256,
    prefetch_batches=2,
    local_shuffle=True,
    shuffle_buffer_size=1000,
):
    process(batch)
```

### iter_batches Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `batch_size` | int | None | Rows per batch |
| `batch_format` | str | "default" | "default", "pandas", "numpy", "arrow" |
| `prefetch_batches` | int | 0 | Number of batches to prefetch |
| `local_shuffle` | bool | False | Shuffle within local iterator |
| `shuffle_buffer_size` | int | None | Buffer size for local shuffle |
| `drop_last` | bool | False | Drop incomplete last batch |

## Execution Plan and Optimization

Ray Data builds a lazy execution plan and optimizes it before execution.

### Execution Plan Inspection

```python
# View the logical execution plan
plan = ds.logical_plan
print(plan)

# View execution plan
plan = ds.execution_plan
print(plan)

# Explain the plan
ds.explain()
```

### Operator Fusion

Ray Data automatically fuses adjacent operators for efficiency:

```python
# These operations will be fused into a single pass
ds = (
    ds.map(lambda row: {"x": row["a"] * 2})
      .filter(lambda row: row["x"] > 0)
      .map(lambda row: {"x": row["x"] + 1})
)
# Fused into a single operator pipeline
```

## Dataset Metadata

```python
# Schema
schema = ds.schema()
print(schema)  # Arrow schema

# Number of rows (may trigger execution)
n = ds.count()

# Number of blocks
n_blocks = ds.num_blocks()

# Size in bytes (estimated)
size = ds.size_bytes()

# Input files
files = ds.input_files()
```

## Dataset I/O with Remote URIs

Ray Data supports reading from and writing to remote storage:

```python
# S3
ds = rd.read_parquet("s3://my-bucket/data/")
ds.write_parquet("s3://my-bucket/output/")

# GCS
ds = rd.read_csv("gs://my-bucket/data/")
ds.write_csv("gs://my-bucket/output/")

# Azure Blob Storage
ds = rd.read_json("az://my-container/data/")

# HDFS
ds = rd.read_parquet("hdfs://namenode:8020/data/")

# Local files
ds = rd.read_parquet("/mnt/data/input/")
ds.write_parquet("/mnt/data/output/")
```

## Performance Tips

### 1. Use map_batches Instead of map

```python
# Slow - row-by-row overhead
ds = ds.map(lambda row: expensive(row))

# Fast - batch processing
ds = ds.map_batches(
    lambda batch: process_batch(batch),
    batch_size=1024,
    batch_format="pandas",
)
```

### 2. Use Actor Pools for Expensive Initialization

```python
# Bad - re-initializes on every task
ds = ds.map(lambda row: load_model().predict(row))

# Good - initialize once per actor
class Predictor:
    def __init__(self):
        self.model = load_model()  # Once per actor

    def __call__(self, batch):
        return self.model.predict(batch)

ds = ds.map_batches(
    Predictor,
    compute=ActorPoolStrategy(size=4),
    batch_size=512,
)
```

### 3. Control Block Size

```python
from ray.data import DataContext

# Larger blocks for fewer tasks (less overhead)
ctx = DataContext.get_current()
ctx.target_max_block_size = 512 * 1024 * 1024  # 512MB

# Smaller blocks for more parallelism
ctx.target_max_block_size = 32 * 1024 * 1024  # 32MB
```

### 4. Repartition Strategically

```python
# Too many small blocks = scheduling overhead
ds = ds.repartition(10)  # Fewer, larger blocks

# Too few large blocks = underutilized cluster
ds = ds.repartition(1000)  # More, smaller blocks

# Rule of thumb: 2-4x the number of cluster CPUs
ds = ds.repartition(num_cpus * 4)
```

### 5. Use Pushdown Predicates for File Formats

```python
# Push filters to file read level (Parquet, Delta)
ds = rd.read_parquet(
    "s3://bucket/data/",
    filter=pa.dataset.field("year") >= 2024,
    columns=["col1", "col2"],  # Column pruning
)
```

### 6. Optimize for GPU Workloads

```python
# Use zero-copy batches and actor pools
ds = ds.map_batches(
    GPUProcessor,
    batch_size=4096,
    zero_copy_batch=True,
    compute=ActorPoolStrategy(size=4),
    num_gpus=1,
    batch_format="numpy",
)
```

### 7. Materialize Shared Intermediate Results

```python
# Bad: recomputed for each downstream use
ds1 = ds.map(expensive_fn).filter(f1)
ds2 = ds.map(expensive_fn).filter(f2)

# Good: materialize once, use many times
cached = ds.map(expensive_fn).materialize()
ds1 = cached.filter(f1)
ds2 = cached.filter(f2)
```

### 8. Use Streaming for Large Datasets

```python
# Process data without materializing entire dataset
for batch in ds.iter_batches(batch_size=1024, prefetch_batches=2):
    write_to_sink(batch)
```

### 9. Set Appropriate Batch Size

```python
# Small batches: more overhead, lower latency
ds = ds.map_batches(fn, batch_size=64)

# Medium batches: good balance
ds = ds.map_batches(fn, batch_size=256)

# Large batches: higher throughput, more memory
ds = ds.map_batches(fn, batch_size=4096)
```

### 10. Use Column Pruning Early

```python
# Select only needed columns before expensive transformations
ds = (
    ds.select_columns(["feature1", "feature2", "label"])
      .map_batches(expensive_transform)
)
```
