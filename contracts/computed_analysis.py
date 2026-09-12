"""Strict deterministic calculation result contract."""
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from contracts.analysis_request import DateRange
class ResultType(StrEnum):
    SCALAR="SCALAR"; TABLE="TABLE"; TIME_SERIES="TIME_SERIES"; PERIOD_COMPARISON="PERIOD_COMPARISON"; GROUPED_PERIOD_COMPARISON="GROUPED_PERIOD_COMPARISON"; RANKING="RANKING"
class ComputedAnalysis(BaseModel):
    model_config=ConfigDict(extra="forbid")
    analysis_id:str=Field(default_factory=lambda:str(uuid4()))
    request_id:str; plan_id:str; dataset_version_id:str; profile_id:str; execution_status:str="COMPLETED"; primary_metric:str; result_type:ResultType
    analysis_period:DateRange|None=None; comparison_period:DateRange|None=None
    columns:list[str]; rows:list[dict]; summary_metrics:dict[str,float|int|None]={}; warnings:list[str]=[]; row_count:int
    created_at:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
    engine_name:str="pandas-analytics-engine"; engine_version:str="4.0.0"; source_dataset_hash:str; plan_hash:str
    @classmethod
    def json_schema(cls): return cls.model_json_schema()
