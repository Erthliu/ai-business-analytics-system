"""Typed, non-executable plan contracts for deterministic analytics."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from contracts.analysis_request import DateRange

class OperationType(StrEnum):
    AGGREGATE="AGGREGATE"; GROUP_BY="GROUP_BY"; PERIOD_COMPARE="PERIOD_COMPARE"; GROUPED_PERIOD_COMPARE="GROUPED_PERIOD_COMPARE"; TREND="TREND"; RANK="RANK"; COUNT_DISTINCT="COUNT_DISTINCT"; FILTER="FILTER"
class Aggregation(StrEnum):
    SUM="SUM"; COUNT="COUNT"; COUNT_DISTINCT="COUNT_DISTINCT"; AVG="AVG"; MIN="MIN"; MAX="MAX"; MEDIAN="MEDIAN"
class FilterOperator(StrEnum):
    EQ="EQ"; NE="NE"; GT="GT"; GTE="GTE"; LT="LT"; LTE="LTE"; IN="IN"; BETWEEN="BETWEEN"
class FilterExpression(BaseModel):
    model_config=ConfigDict(extra="forbid")
    field: str
    operator: FilterOperator
    value: object
class Operation(BaseModel):
    model_config=ConfigDict(extra="forbid")
    type: OperationType
    field: str | None = None
    aggregation: Aggregation | None = None
    dimension: str | None = None
class AnalysisPlan(BaseModel):
    model_config=ConfigDict(extra="forbid")
    plan_id:str=Field(default_factory=lambda:str(uuid4()))
    request_id:str; dataset_version_id:str; profile_id:str; primary_metric:str
    secondary_metrics:list[str]=[]; dimensions:list[str]=[]; filters:list[FilterExpression]=[]
    analysis_period:DateRange|None=None; comparison_period:DateRange|None=None
    operations:list[Operation]; sort:str|None=None; sort_descending:bool=True; limit:int|None=None; time_bucket:str|None=None; time_field:str="order_date"; assumptions:list[str]=[]
    metric_registry_version:str="1.0.0"
    created_at:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    planner_name:str="analytics-planner"; planner_version:str="4.0.0"; prompt_version:str="planner-v1"
    provider_name:str|None=None; model_name:str|None=None
    @classmethod
    def json_schema(cls): return cls.model_json_schema()
