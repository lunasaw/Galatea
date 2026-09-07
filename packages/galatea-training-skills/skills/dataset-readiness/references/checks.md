# Readiness checks

Require an immutable manifest digest, split digest, preprocessing identity, distinct versioned objects for train, validation, and test, and confirmation that test remains untouched. Check that the registered task and metric accept the schema, target, sample units, and population. Record missing values, duplicates across splits, temporal or subject leakage, label leakage, class/target coverage, corrupt records, and provenance limits when the platform supplies them.

Only report supported conclusions. Never read the test view for profiling or selection. If remediation changes population, splits, preprocessing, or labels, require a new registered dataset identity and request revision.
