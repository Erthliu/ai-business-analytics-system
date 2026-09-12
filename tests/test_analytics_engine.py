"""Core deterministic V4 analytics tests."""
import pandas as pd
import pytest
from agents.analytics_engine import AnalyticsEngine
from agents.analytics_planner import AnalyticsPlanner
from agents.requirement_agent import RequirementAgent
from contracts.dataset_profile import ColumnProfile, DatasetProfile
from contracts.analysis_plan import AnalysisPlan, FilterExpression, FilterOperator, Operation, OperationType
from analytics.metric_registry import MetricRegistry

def _profile():
    return DatasetProfile(profile_id="p",dataset_version_id="v",pipeline_run_id="r",row_count=2,column_count=2,schema_fingerprint="s",profiler_version="2",columns=[
        ColumnProfile(name="revenue",dtype="float64",null_count=0,null_percentage=0,unique_count=2,uniqueness_ratio=1),
        ColumnProfile(name="region",dtype="object",null_count=0,null_percentage=0,unique_count=2,uniqueness_ratio=1)])

def test_grouped_revenue_is_deterministic():
    request=RequirementAgent().interpret("Compare revenue by region",_profile())
    plan=AnalyticsPlanner().build(request,_profile())
    result=AnalyticsEngine().execute(pd.DataFrame({"revenue":[10.,20.],"region":["North","South"]}),plan,"hash")
    assert result.row_count==2
    assert result.result_type=="TABLE"

def _plan(metric, **kwargs):
    return AnalysisPlan(request_id="r",dataset_version_id="v",profile_id="p",primary_metric=metric,operations=[Operation(type=OperationType.AGGREGATE,field=metric)],**kwargs)

def test_registry_derived_metrics_and_filters():
    frame=pd.DataFrame({"revenue":[10.,20.,30.],"quantity":[1,2,3],"order_id":["1","2","2"],"customer_id":["a","b","a"],"region":["N","S","N"]})
    engine=AnalyticsEngine()
    assert engine.execute(frame,_plan("order_count"),"h").rows[0]["order_count"]==2
    assert engine.execute(frame,_plan("customer_count"),"h").rows[0]["customer_count"]==2
    assert engine.execute(frame,_plan("average_order_value"),"h").rows[0]["average_order_value"]==30
    plan=_plan("revenue",filters=[FilterExpression(field="region",operator=FilterOperator.EQ,value="N")])
    assert engine.execute(frame,plan,"h").rows[0]["revenue"]==40
    with pytest.raises(ValueError): MetricRegistry().get("profit")
