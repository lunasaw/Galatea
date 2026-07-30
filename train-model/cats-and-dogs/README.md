# 猫狗分类 Notebook 运行说明

本文档覆盖从下载 Microsoft Cats vs Dogs 数据集，到启动并执行
`cats-vs-dogs-classification.ipynb` 的完整流程。

## 1. 目录约定

本项目使用以下固定路径：

```text
/data/ai/chenzhangyue/code/
├── data/cats-and-dogs/
│   ├── microsoft-catsvsdogs-dataset.zip
│   └── PetImages/
│       ├── Cat/                 # 12,500 张
│       └── Dog/                 # 12,500 张
└── train/cats-and-dogs/
    ├── README.md
    └── cats-vs-dogs-classification.ipynb
```

Notebook 直接读取已经解压的 `PetImages`，不会在训练时修改原始数据。

## 2. 安装运行环境

推荐使用 Python 3.12。在当前 Jupyter Python 环境中安装依赖：

```bash
python -m pip install tensorflow pillow matplotlib pandas numpy jupyter
```

确认 TensorFlow 可以导入：

```bash
python -c "import tensorflow as tf; print(tf.__version__); print(tf.config.list_physical_devices())"
```

当前环境已验证可使用 TensorFlow 2.21。若输出中只有 `CPU:0`，notebook
仍然可以执行，但完整训练会明显慢于 GPU。

## 3. 下载数据集

先安装并配置 Kaggle CLI。Kaggle API Token 可以放在 `~/.kaggle/kaggle.json`，
也可以通过平台提供的 Kaggle 凭据完成认证。

```bash
python -m pip install kaggle
mkdir -p /data/ai/chenzhangyue/code/data/cats-and-dogs
kaggle datasets download \
  -d shaunthesheep/microsoft-catsvsdogs-dataset \
  -p /data/ai/chenzhangyue/code/data/cats-and-dogs \
  --force
```

使用 `--force` 可以避免上一次中断下载留下的续传片段混入 zip。

## 4. 解压并检查数据

```bash
unzip -q \
  /data/ai/chenzhangyue/code/data/cats-and-dogs/microsoft-catsvsdogs-dataset.zip \
  -d /data/ai/chenzhangyue/code/data/cats-and-dogs
```

检查两类图片数量：

```bash
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Cat \
  -maxdepth 1 -type f -iname '*.jpg' | wc -l
find /data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/Dog \
  -maxdepth 1 -type f -iname '*.jpg' | wc -l
```

两条命令都应输出 `12500`。原始数据集包含两张已知坏图：
`PetImages/Cat/666.jpg` 和 `PetImages/Dog/11702.jpg`。Notebook 在划分数据时会
检查图片并自动跳过它们，不需要手工删除原始文件。

如果 `unzip` 报告 `bad zipfile offset` 或 `extra bytes`，说明下载文件已损坏，
不要使用部分解压出的数据；请重新执行第 3 节带 `--force` 的下载命令，再解压。

## 5. 启动 Notebook

如果平台已经在 `8888` 端口启动了 Jupyter，直接打开该服务，在文件浏览器中进入
`cats-and-dogs/cats-vs-dogs-classification.ipynb`，不需要再启动一个服务。

尚未启动 Jupyter 时运行：

```bash
cd /data/ai/chenzhangyue/code/train
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser
```

打开后确认 Kernel 使用的是安装了 TensorFlow 的 Python 3 环境，然后选择
`Kernel -> Restart Kernel and Run All Cells`。

Notebook 每次从头执行时会：

1. 检查 `PetImages/Cat` 和 `PetImages/Dog` 是否完整；
2. 验证图片并跳过两张坏图；
3. 以固定随机种子划分 90% 训练集、5% 验证集和 5% 测试集；
4. 在 `/tmp/cats-v-dogs` 创建临时副本；
5. 训练基础 CNN，执行测试、预测和 Grad-CAM；
6. 再训练一个带数据增强的 CNN，并绘制训练曲线。

`/tmp/cats-v-dogs` 会在每次重新执行时重建，不会导致重复样本。系统重启后该
临时目录消失是正常现象，下次运行 notebook 会自动重新生成。

## 6. 先做快速验证

第一次运行建议把导入单元中的：

```python
EPOCHS = 10
```

临时改为：

```python
EPOCHS = 1
```

确认数据加载、训练、测试和可视化都正常后，再改回 `10` 进行正式训练。该变量
同时控制基础模型和数据增强模型，因此默认完整运行总共会训练 20 个 epoch。

## 7. 常见问题

- `ModuleNotFoundError: No module named 'tensorflow'`：TensorFlow 安装到了不同的
  Python 环境；在 notebook 中运行 `import sys; print(sys.executable)`，再用该
  Python 的 `-m pip install tensorflow` 安装。
- `FileNotFoundError: Extract the dataset first`：确认目录名大小写严格为
  `PetImages/Cat` 和 `PetImages/Dog`，并重新执行第 4 节的检查命令。
- CUDA 或 `no CUDA-capable device` 提示：当前进程没有可用 GPU；这是性能提示，
  不会阻止 CPU 训练。
- 训练时间过长：先设置 `EPOCHS = 1`；正式训练建议在能够识别 GPU 的 Jupyter
  内核中运行。
