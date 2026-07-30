ray start --head --node-ip-address=10.48.1.123 \
  --port=6379 \
  --dashboard-host=0.0.0.0 \
  --dashboard-port=8265 \
  --num-gpus=4 \
  --resources='{"accelerator_type:L20": 4, "accelerator_type:B300": 4, "accelerator_type:A100": 4}'
