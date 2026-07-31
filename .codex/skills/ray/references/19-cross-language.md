# Cross-Language Support

## Overview

Ray supports cross-language invocation, allowing tasks and actors written in one language to call tasks and actors written in another language.

## Supported Languages

| Language | Status | Version |
|----------|--------|---------|
| Python | Stable | 3.8+ |
| Java | Stable | 8+ |
| C++ | Stable | C++17 |

## Cross-Language Task Invocation

### Python Calling Java
```python
import ray

# Call a Java remote function
result = ray.java_function("com.example.MyClass", "myMethod").remote(arg1, arg2)
output = ray.get(result)
```

### Python Calling C++
```python
import ray

# Call a C++ remote function
result = ray.cpp_function("my_namespace.my_function").remote(arg1)
output = ray.get(result)
```

### Java Calling Python
```java
// Java code
import io.ray.api.Ray;
import io.ray.api.ObjectRef;
import io.ray.api.PyActorHandle;

// Call Python function
ObjectRef<String> ref = Ray.task(PyFunction.of("my_module", "my_function", String.class), arg).remote();
String result = ref.get();
```

### C++ Calling Python
```cpp
#include "ray/api.h"

// Call Python function
auto ref = ray::Task(PyFunction("my_module", "my_function"))
    .Remote(arg1, arg2);
auto result = ref.Get();
```

## Cross-Language Actors

### Python Using Java Actor
```python
import ray

# Create a Java actor
handle = ray.java_actor("com.example.MyActor").remote()

# Call methods on the Java actor
result = handle.java_method("process", arg1).remote()
output = ray.get(result)
```

### Java Using Python Actor
```java
// Create a Python actor from Java
PyActorHandle handle = Ray.actor(PyActorClass.of("my_module", "MyClass")).remote();

// Call methods
ObjectRef result = handle.task(PyMethod.of("my_method"), arg).remote();
Object output = result.get();
```

## Type System

### Supported Types for Cross-Language Calls

| Python | Java | C++ |
|--------|------|-----|
| `int` | `Integer` / `int` | `int` |
| `float` | `Double` / `double` | `double` |
| `bool` | `Boolean` / `boolean` | `bool` |
| `str` | `String` | `std::string` |
| `bytes` | `byte[]` | `std::string` |
| `list` | `List<?>` | `std::vector` |
| `dict` | `Map<?,?>` | `std::unordered_map` |
| `numpy.ndarray` | - | - |
| `None` | `null` | `nullptr` |
| `ObjectRef` | `ObjectRef` | `ObjectRef` |

### Serialization
- Cross-language data is serialized using **Apache Arrow**
- Compatible types are automatically converted
- Complex types may require custom serialization

## Java API

### Initialization
```java
import io.ray.api.Ray;

// Initialize Ray
Ray.init();

// Connect to existing cluster
Ray.init("address");
```

### Remote Functions
```java
import io.ray.api.Ray;
import io.ray.api.ObjectRef;

// Define remote function
@RayRemote
public static int square(int x) {
    return x * x;
}

// Call remotely
ObjectRef<Integer> ref = Ray.task(Example::square, 5).remote();
int result = ref.get();
```

### Remote Actors
```java
@RayRemote
public class Counter {
    private int count = 0;

    public int increment(int delta) {
        count += delta;
        return count;
    }

    public int getCount() {
        return count;
    }
}

// Create actor
ActorHandle<Counter> counter = Ray.actor(Counter::new).remote();

// Call methods
ObjectRef<Integer> ref = counter.task(Counter::increment, 1).remote();
int result = ref.get();
```

### Actor Options
```java
ActorHandle<Counter> counter = Ray.actor(Counter::new)
    .setNumCpus(2)
    .setNumGpus(1)
    .setMemory(1024 * 1024 * 1024)  // 1 GB
    .setName("my-counter")
    .setNamespace("my-namespace")
    .setLifetime("detached")
    .remote();
```

### Resources
```java
// Task with resources
ObjectRef<String> ref = Ray.task(MyClass::myMethod, arg)
    .setNumCpus(2)
    .setNumGpus(1)
    .setResources(Map.of("TPU", 2.0))
    .remote();
```

### Placement Groups (Java)
```java
import io.ray.api.placementgroup.PlacementGroup;
import io.ray.api.placementgroup.PlacementStrategy;

PlacementGroup pg = PlacementGroupFactory.createPlacementGroup(
    Arrays.asList(
        Bundle.of(Map.of("CPU", 2)),
        Bundle.of(Map.of("GPU", 1))
    ),
    PlacementStrategy.PACK
);

Ray.get(pg.ready());

// Use placement group
ActorHandle<MyActor> actor = Ray.actor(MyActor::new)
    .setPlacementGroup(pg, 0)
    .remote();
```

## C++ API

### Initialization
```cpp
#include "ray/api.h"

// Initialize
ray::init();

// Connect to cluster
ray::init("address");
```

### Remote Functions
```cpp
// Define remote function
RAY_REMOTE(int square(int x) {
    return x * x;
});

// Call remotely
auto ref = ray::Task(square).Remote(5);
auto result = ref.Get();
```

### Remote Actors
```cpp
class Counter {
public:
    int increment(int delta) {
        count_ += delta;
        return count_;
    }
    int getCount() {
        return count_;
    }
private:
    int count_ = 0;
};

// Factory function for actor creation
static Counter *CreateCounter() { return new Counter(); }

// Register actor
RAY_REMOTE(CreateCounter, &Counter::increment, &Counter::getCount);

// Create actor
auto counter = ray::Actor(CreateCounter).Remote();
auto ref = counter.Task(&Counter::increment).Remote(1);
auto result = ref.Get();
```

### C++ Actor Options
```cpp
auto counter = ray::Actor(CreateCounter)
    .SetNumCpus(2)
    .SetNumGpus(1)
    .SetName("my-counter")
    .Remote();
```

## Naming and Discovery

### Global Names
```python
# Python - register with global name
@ray.remote
class MyActor:
    pass

actor = MyActor.options(
    name="global_actor",
    namespace="shared_ns",
).remote()
```

```java
// Java - get actor by name
Optional<ActorHandle> handle = Ray.getActor("global_actor", "shared_ns");
```

```cpp
// C++ - get actor by name
auto handle = ray::GetActor("global_actor", "shared_ns");
```

### Namespaces
```python
# Python - set namespace
ray.init(namespace="my_namespace")

# Get actor from specific namespace
handle = ray.get_actor("my_actor", namespace="other_namespace")
```

## Best Practices

1. **Use simple types** for cross-language arguments (primitives, lists, dicts)
2. **Register functions/actors** before cross-language calls
3. **Use global names** for actors that need cross-language access
4. **Test serialization** of complex types before production use
5. **Handle type mismatches** - not all types convert cleanly
6. **Use namespaces** to avoid naming conflicts across languages
7. **Consider performance** overhead of cross-language serialization
8. **Keep interfaces simple** between language boundaries
