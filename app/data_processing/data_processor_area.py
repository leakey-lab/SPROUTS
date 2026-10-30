from app.config import AREA_VALUE_COLUMN
from app.data_processing.data_processor import MetricDataProcessor


class DataProcessorArea(MetricDataProcessor):
    """Root-area data processor (shares MetricDataProcessor; area metric column)."""

    value_column = AREA_VALUE_COLUMN
    metric_label = "Area"
    unit = "mm²"
