# Ray RLlib

## Overview

Ray RLlib is an industry-grade reinforcement learning library providing distributed training for RL algorithms.

## Available Algorithms

| Algorithm | Type | Action Space | Multi-Agent |
|-----------|------|-------------|-------------|
| **PPO** | On-Policy | Continuous/Discrete | Yes |
| **APPO** | On-Policy (Async) | Continuous/Discrete | Yes |
| **IMPALA** | On-Policy (Distributed) | Discrete | Yes |
| **DQN** | Off-Policy | Discrete | Yes |
| **SAC** | Off-Policy | Continuous/Discrete | Yes |
| **DDPG** | Off-Policy | Continuous | No |
| **TD3** | Off-Policy | Continuous | No |
| **A3C** | On-Policy | Continuous/Discrete | Yes |
| **ES** | Evolutionary | Continuous/Discrete | No |
| **ARS** | Evolutionary | Continuous/Discrete | No |
| **PG** | Policy Gradient | Continuous/Discrete | No |
| **MARWIL** | Offline RL | Continuous/Discrete | No |
| **CQL** | Offline RL | Continuous | No |
| **BC** | Imitation | Continuous/Discrete | No |
| **DreamerV3** | Model-Based | Continuous/Discrete | No |

## AlgorithmConfig API

```python
from ray.rllib.algorithms.ppo import PPOConfig

config = (
    PPOConfig()
    .environment("CartPole-v1")
    .framework("torch")
    .rollouts(num_rollout_workers=4)
    .training(
        lr=3e-4,
        gamma=0.99,
        train_batch_size=4000,
        sgd_minibatch_size=128,
        num_sgd_iter=30,
    )
    .resources(num_gpus=1)
)

algo = config.build()
for i in range(100):
    result = algo.train()
    print(f"Reward: {result['episode_reward_mean']}")
```

## Configuration Methods

### .environment()
```python
config.environment(
    env="CartPole-v1",           # Env name or class
    env_config={},               # Env constructor kwargs
    observation_space=None,      # Override observation space
    action_space=None,           # Override action space
    render_env=False,            # Enable rendering
    clip_rewards=True,           # Clip rewards
    normalize_actions=True,      # Normalize action space
)
```

### .training()
```python
# PPO-specific
config.training(
    lr=3e-4,                        # Learning rate
    gamma=0.99,                     # Discount factor
    train_batch_size=4000,          # Total batch per iteration
    sgd_minibatch_size=128,         # Minibatch for SGD
    num_sgd_iter=30,                # SGD epochs per batch
    clip_param=0.3,                 # PPO clipping
    vf_loss_coeff=1.0,             # Value function loss weight
    entropy_coeff=0.0,             # Entropy bonus
    kl_coeff=0.2,                  # KL penalty coefficient
    kl_target=0.01,                # Target KL divergence
    use_gae=True,                  # Generalized Advantage Estimation
    lambda_=1.0,                   # GAE lambda
    grad_clip=None,                # Gradient clipping
    lr_schedule=None,              # Learning rate schedule
)
```

### .env_runners()
```python
config.env_runners(
    num_env_runners=4,              # Parallel env runners
    num_envs_per_env_runner=1,      # Vectorized envs per worker
    rollout_fragment_length=200,    # Steps per rollout
    batch_mode="truncate_episodes", # "complete_episodes" or "truncate"
    observation_filter="NoFilter",  # "MeanStdFilter"
    preprocessor_pref="deepmind",   # Preprocessor preference
)
```

### .learners()
```python
config.learners(
    num_learners=2,                 # Learner workers
    num_gpus_per_learner=1,        # GPUs per learner
    num_cpus_per_learner=1,        # CPUs per learner
)
```

### .evaluation()
```python
config.evaluation(
    evaluation_interval=5,          # Evaluate every N iterations
    evaluation_num_env_runners=1,
    evaluation_duration=10,
    evaluation_config={},           # Override config for evaluation
)
```

### .multi_agent()
```python
config.multi_agent(
    policies={
        "policy1": PolicySpec(
            observation_space=gym.spaces.Box(...),
            action_space=gym.spaces.Discrete(2),
            config={},
        ),
        "policy2": PolicySpec(...),
    },
    policy_mapping_fn=lambda agent_id, episode, **kw: "policy1",
    policies_to_train=["policy1"],
)
```

### .resources()
```python
config.resources(
    num_gpus=1,                     # GPUs for learner
    num_cpus_per_worker=1,
    num_gpus_per_worker=0,
)
```

## Model Configuration

```python
config.rl_module(
    model_config={
        "fcnet_hiddens": [256, 256],       # Hidden layers
        "fcnet_activation": "relu",         # Activation
        "vf_share_layers": False,           # Share value function
        "use_lstm": False,                  # LSTM layer
        "lstm_cell_size": 256,             # LSTM size
        "conv_filters": None,              # CNN filters
        "post_fcnet_hiddens": [512],       # Post-FC layers
        "log_std_init": -3.0,             # Initial log_std
        "ortho_init": False,              # Orthogonal init
    }
)
```

## Environments

### Single Agent
```python
# Gymnasium
config.environment("CartPole-v1")

# Custom environment
class MyEnv(gym.Env):
    def __init__(self, config=None):
        pass
    def reset(self, *, seed=None, options=None):
        return obs, info
    def step(self, action):
        return obs, reward, terminated, truncated, info

config.environment(env=MyEnv, env_config={"param": "value"})
```

### Multi-Agent
```python
from ray.rllib.env.multi_agent_env import MultiAgentEnv

class MyMultiAgentEnv(MultiAgentEnv):
    def reset(self, *, seed=None, options=None):
        return {agent_id: obs for agent_id in self.possible_agents}, {}

    def step(self, action_dict):
        return (
            {agent_id: obs},      # observations
            {agent_id: reward},    # rewards
            {agent_id: terminated}, # terminated
            {agent_id: truncated}, # truncated
            {agent_id: info},      # infos
        )

    @property
    def possible_agents(self):
        return ["agent0", "agent1"]
```

## Checkpointing

```python
# Save
checkpoint_path = algo.save_to_path("/tmp/checkpoints")

# Restore
algo.restore_from_path(checkpoint_path)

# From checkpoint
algo = Algorithm.from_checkpoint(checkpoint_path)
```

## Callbacks

```python
from ray.rllib.algorithms.callbacks import RLlibCallback

class MyCallback(RLlibCallback):
    def on_algorithm_init(self, *, algorithm, **kwargs):
        pass

    def on_train_result(self, *, algorithm, result, **kwargs):
        print(f"Reward: {result['episode_reward_mean']}")

    def on_episode_end(self, *, episode, **kwargs):
        print(f"Return: {episode.get_return()}")

config.callbacks(callbacks_class=MyCallback)
```

## Replay Buffers

```python
config.training(
    replay_buffer_config={
        "type": "PrioritizedReplayBuffer",
        "capacity": 10000,
        "storage_unit": "timesteps",  # timesteps, sequences, episodes
        "prioritized_replay_alpha": 0.6,
        "prioritized_replay_beta": 0.4,
        "prioritized_replay_eps": 1e-6,
    }
)
```

## Offline Training

```python
config.offline_data(
    input="/path/to/data",
    input_format="json",  # json, parquet
    batch_size=256,
    shuffle=True,
    repeat_after_epoch=True,
)
```
