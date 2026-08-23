from typing import Optional
from agent.agent_coordinator import run as coordinator_run

class BasePipeline:
    def run(
        self,
        user_message: str,
        router_db: Optional[str],
        session: dict,
        session_id: Optional[str],
        history: list,
        target_db: Optional[str],
        decision: Optional[object] = None,
    ) -> dict:
        """
        Executes the pipeline logic.
        """
        raise NotImplementedError()

class StandardSQLPipeline(BasePipeline):
    runner_name = "STANDARD_SQL"

    def run(
        self,
        user_message: str,
        router_db: Optional[str],
        session: dict,
        session_id: Optional[str],
        history: list,
        target_db: Optional[str],
        decision: Optional[object] = None,
    ) -> dict:
        """
        Executes the standard SQL orchestration loop using the Agent Coordinator.
        """
        return coordinator_run(
            user_message=user_message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            decision=decision,
        )

class AnalyticsPipeline(BasePipeline):
    runner_name = "STANDARD_ANALYTICS"

    def run(
        self,
        user_message: str,
        router_db: Optional[str],
        session: dict,
        session_id: Optional[str],
        history: list,
        target_db: Optional[str],
        decision: Optional[object] = None,
    ) -> dict:
        """
        Routes analytics requests through the single-pipeline Agent Coordinator
        with pipeline_hint="ANALYTICS_ENGINE" to ensure raw unaggregated rows
        are returned for downstream pandas math.
        """
        from agent.agent_coordinator import run as coordinator_run
        return coordinator_run(
            user_message=user_message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            pipeline_hint="ANALYTICS_ENGINE",
            decision=decision,
        )


class VisualizationPipeline(BasePipeline):
    """
    Visualization Engine pipeline (Phase 10.4).
    Routes through single-pipeline Agent Coordinator with pipeline_hint="VISUALIZATION".
    """
    runner_name = "VISUALIZATION_ENGINE"

    def run(
        self,
        user_message: str,
        router_db: Optional[str],
        session: dict,
        session_id: Optional[str],
        history: list,
        target_db: Optional[str],
        decision: Optional[object] = None,
    ) -> dict:
        from agent.agent_coordinator import run as coordinator_run
        return coordinator_run(
            user_message=user_message,
            router_db=router_db,
            session=session,
            session_id=session_id,
            history=history,
            target_db=target_db,
            bypass_planner=False,
            pipeline_hint="VISUALIZATION",
            decision=decision,
        )


# Standardized Pipeline Name constants
STANDARD = "STANDARD"
ANALYTICS = "ANALYTICS"
ANALYTICS_ENGINE = "ANALYTICS_ENGINE"
VISUALIZATION = "VISUALIZATION"
REPORTING = "REPORTING"
EXPORT = "EXPORT"
SAMPLE_DATA = "SAMPLE_DATA"
SQL_EXPLANATION = "SQL_EXPLANATION"
STANDARD_SQL = "STANDARD_SQL"  # The fallback runner name

# Pipeline Registry
_REGISTRY = {
    STANDARD_SQL: StandardSQLPipeline(),
    STANDARD: StandardSQLPipeline(),
    ANALYTICS: StandardSQLPipeline(),
    ANALYTICS_ENGINE: AnalyticsPipeline(),
    VISUALIZATION: VisualizationPipeline(),
    REPORTING: StandardSQLPipeline(),
    EXPORT: StandardSQLPipeline(),
    SAMPLE_DATA: StandardSQLPipeline(),
    SQL_EXPLANATION: StandardSQLPipeline(),
}

def get_pipeline(pipeline_name: str) -> BasePipeline:
    """
    Returns the registered pipeline instance for the given name.
    Falls back to StandardSQLPipeline if the name is unregistered.
    """
    return _REGISTRY.get(pipeline_name, _REGISTRY[STANDARD_SQL])
