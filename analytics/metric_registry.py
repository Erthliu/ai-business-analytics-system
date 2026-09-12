"""Small, versioned semantic metric registry."""
from dataclasses import dataclass
from enum import StrEnum

class MetricOperation(StrEnum):
    AGGREGATE = "AGGREGATE"
    RATIO = "RATIO"

class MetricAdditivity(StrEnum):
    ADDITIVE = "ADDITIVE"
    SEMI_ADDITIVE = "SEMI_ADDITIVE"
    DERIVED = "DERIVED"

@dataclass(frozen=True)
class MetricDefinition:
    metric_name: str
    label: str
    operation: MetricOperation
    source_fields: tuple[str, ...]
    aggregation: str
    result_type: str
    description: str
    required_fields: tuple[str, ...]
    additivity: MetricAdditivity

class MetricRegistry:
    version = "1.0.0"
    _metrics = {
        "revenue": MetricDefinition("revenue","Revenue",MetricOperation.AGGREGATE,("revenue",),"SUM","currency","Sum of revenue.",("revenue",),MetricAdditivity.ADDITIVE),
        "units_sold": MetricDefinition("units_sold","Units sold",MetricOperation.AGGREGATE,("quantity",),"SUM","number","Sum of quantity.",("quantity",),MetricAdditivity.ADDITIVE),
        "order_count": MetricDefinition("order_count","Order count",MetricOperation.AGGREGATE,("order_id",),"COUNT_DISTINCT","integer","Distinct orders.",("order_id",),MetricAdditivity.SEMI_ADDITIVE),
        "customer_count": MetricDefinition("customer_count","Customer count",MetricOperation.AGGREGATE,("customer_id",),"COUNT_DISTINCT","integer","Distinct customers.",("customer_id",),MetricAdditivity.SEMI_ADDITIVE),
        "average_order_value": MetricDefinition("average_order_value","Average order value",MetricOperation.RATIO,("revenue","order_id"),"SUM_DIV_COUNT_DISTINCT","currency","Revenue divided by distinct orders.",("revenue","order_id"),MetricAdditivity.DERIVED),
    }
    def get(self, name: str) -> MetricDefinition:
        try: return self._metrics[name]
        except KeyError as error: raise ValueError(f"Unknown metric: {name}") from error
    def names(self) -> tuple[str, ...]:
        """Return the stable set of registered semantic metric names."""
        return tuple(sorted(self._metrics))
