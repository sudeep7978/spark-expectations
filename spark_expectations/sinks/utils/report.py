from dataclasses import dataclass
from pyspark.sql import DataFrame
from pyspark.sql.functions import *
from spark_expectations.core.context import SparkExpectationsContext
from spark_expectations.core.exceptions import SparkExpectationsMiscException
from spark_expectations.notifications.push.alert import AlertTrial
# from alert_trial import AlertTrial  # Import AlertTrial
from pyspark.sql.functions import lit
from pyspark.sql.functions import col, lit, split, regexp_extract, regexp_replace, round, explode, expr, trim, \
    coalesce, when, size, concat_ws, abs, filter, regexp_replace
from pyspark.sql.types import DateType, StringType, TimestampType, DoubleType, DecimalType
import time


@dataclass
class SparkExpectationsReport:
    _context: SparkExpectationsContext

    def __post_init__(self) -> None:
        self.spark = self._context.spark

    def dq_obs_report_data_insert(self) -> DataFrame:
        try:
            context = self._context
            print("dq_obs_report_data_insert method called stats_detailed table")
            df_stats_detailed = context.get_stats_detailed_dataframe
            df_custom_detailed = context.get_custom_detailed_dataframe
            df=df_custom_detailed
            dq_status_calculation_attribute = "success_percentage"
            source_zero_and_target_zero_is = "pass"
            df = df.filter((df.source_output.isNotNull()) & (df.target_output.isNotNull()))
            df = df.withColumn("success_percentage", lit(None).cast(DoubleType())) \
                .withColumn("failed_records", lit(0)) \
                .withColumn("status", lit(None).cast(StringType())) \
                .withColumnRenamed("source_output", "total_records") \
                .withColumnRenamed("target_output", "valid_records")
            join_columns = ["run_id", "product_id", "table_name", "column_name", "rule"]
            only_querydq_src_base_df = df
            only_querydq_src_base_df = only_querydq_src_base_df.withColumn('extracted_total_records_data', split(
                regexp_extract('total_records', r'=\[(.*)\]', 1), '},'))
            only_querydq_src_df = only_querydq_src_base_df.select('*', explode('extracted_total_records_data').alias(
                'total_records_dict'))
            only_querydq_src_df = only_querydq_src_df.withColumn('total_records_dict_split', split(
                regexp_replace(col('total_records_dict'), '[}{]', ''), ','))

            only_querydq_src_df = only_querydq_src_df.withColumn(
                'total_records',
                expr("element_at(total_records_dict_split, -1)")
            )
            only_querydq_src_df = only_querydq_src_df.withColumn(
                'column_name',
                when(size(col('total_records_dict_split')) > 1,
                     concat_ws(",", expr("slice(total_records_dict_split, 1, size(total_records_dict_split)-1)")))
                .otherwise(col('column_name'))
            )

            data_types = {
                "rule": StringType(),
                "column_name": StringType(),
                "dq_time": TimestampType(),
                "product_id": StringType(),
                "table_name": StringType(),
                "status": StringType(),
                "total_records": StringType(),
                "failed_records": StringType(),
                "valid_records": StringType(),
                "success_percentage": StringType(),
                "run_id": StringType()
            }
            dq_column_list = [col_name for col_name in data_types.keys()]
            src_dq_column_list = [col_name for col_name in dq_column_list if col_name not in ['valid_records']]
            only_querydq_src_final_df = only_querydq_src_df.selectExpr(*src_dq_column_list)

            only_querydq_tgt_base_df = df
            only_querydq_tgt_base_df = only_querydq_tgt_base_df.withColumn('extracted_valid_records_data', split(
                regexp_extract('valid_records', r'=\[(.*)\]', 1), '},'))
            only_querydq_tgt_df = only_querydq_tgt_base_df.select('*', explode('extracted_valid_records_data').alias(
                'valid_records_dict'))
            only_querydq_tgt_df = only_querydq_tgt_df.withColumn('total_valid_dict_split', split(
                regexp_replace(col('valid_records_dict'), '[}{]', ''), ','))
            only_querydq_tgt_df = only_querydq_tgt_df.withColumn(
                'valid_records',
                expr("element_at(total_valid_dict_split, -1)")
            )
            only_querydq_tgt_df = only_querydq_tgt_df.withColumn(
                'column_name',
                when(size(col('total_valid_dict_split')) > 1,
                     concat_ws(",", expr("slice(total_valid_dict_split, 1, size(total_valid_dict_split)-1)")))
                .otherwise(col('column_name'))
            )

            tgt_dq_column_list = [col_name for col_name in dq_column_list if col_name not in ['total_records']]
            only_querydq_tgt_final_df = only_querydq_tgt_df.selectExpr(*tgt_dq_column_list)

            ignore_colums = ["valid_records", "total_records"]
            only_querydq_src_final_df = only_querydq_src_final_df.select(
                [col(c).alias('src_' + c) if c not in ignore_colums else col(c).alias(c) for c in
                 only_querydq_src_final_df.columns])
            only_querydq_src_final_df.createOrReplaceTempView("src_df")
            only_querydq_tgt_final_df = only_querydq_tgt_final_df.select(
                [col(c).alias('tgt_' + c) if c not in ignore_colums else col(c).alias(c) for c in
                 only_querydq_tgt_final_df.columns])
            only_querydq_tgt_final_df.createOrReplaceTempView("tgt_df")

            trim_col_list = 'column_name'
            sql_query = "SELECT " + ", ".join(
                [f"COALESCE(src_df.src_{col}, tgt_df.tgt_{col}) AS {col}" if col not in ignore_colums else f"{col}" for
                 col
                 in dq_column_list]) + " FROM src_df FULL JOIN tgt_df ON " + " AND ".join(
                [
                    f"REGEXP_REPLACE(REGEXP_REPLACE(lower(src_df.src_{col}), '\"', ''), ' ', '') = REGEXP_REPLACE(REGEXP_REPLACE(lower(tgt_df.tgt_{col}), '\"', ''), ' ', '')" if col not in ignore_colums else f"src_df.{col} = tgt_df.{col} "
                    for
                    col in join_columns])
            # Execute the SQL query
            only_querydq_final_after_join_df = self.spark.sql(sql_query)

            only_querydq_final_after_join_df = (only_querydq_final_after_join_df.withColumn('total_records_only_nbr',
                                                                                            regexp_extract(
                                                                                                col('total_records'),
                                                                                                r'\d+', 0).cast(
                                                                                                'bigint'))
                                                .withColumn('valid_records_only_nbr',
                                                            regexp_extract(col('valid_records'), r'\d+', 0).cast(
                                                                'bigint')))

            only_querydq_final_after_join_df = only_querydq_final_after_join_df.withColumn(
                'success_percentage',
                when((col('total_records_only_nbr') == '') & (col('valid_records_only_nbr').isNull()), lit(100))
                .when((col('total_records_only_nbr') == '') & (col('valid_records_only_nbr') == ''), lit(100))
                .when((col('total_records_only_nbr') != '') & (col('valid_records_only_nbr').isNull()), lit(0))
                .otherwise(
                    coalesce(
                        (
                                100 * coalesce(trim(col('valid_records_only_nbr')), lit(0)) /
                                coalesce(trim(col('total_records_only_nbr')), lit(0))
                        ).cast(DecimalType(20, 2)),
                        lit(0)
                    )
                )
            )
            only_querydq_final_after_join_df = only_querydq_final_after_join_df.withColumn(
                'failed_rec_perc_variance',
                when((col('total_records_only_nbr') == '') & (col('valid_records_only_nbr').isNull()), lit(0))
                .when((col('total_records_only_nbr') == '') & (col('valid_records_only_nbr') == ''), lit(0))
                .when((col('total_records_only_nbr') != '') & (col('valid_records_only_nbr').isNull()), lit(100))
                .when(
                    (coalesce(col('total_records_only_nbr'), lit(0)) != 0) &
                    (coalesce(col('valid_records_only_nbr'), lit(0)) != 0),
                    coalesce(
                        round(
                            ((col('total_records_only_nbr') - col('valid_records_only_nbr')) / col(
                                'total_records_only_nbr')) * 100,
                            2
                        ),
                        lit(0)
                    )
                )
                .otherwise(100)
            )
            only_querydq_final_after_join_df = only_querydq_final_after_join_df.withColumn('failed_records',
                                                                                           coalesce(coalesce(trim(
                                                                                               col('total_records_only_nbr')).cast(
                                                                                               'bigint'), lit(0)) -
                                                                                                    coalesce(trim(
                                                                                                        col('valid_records_only_nbr')).cast(
                                                                                                        'bigint'),
                                                                                                        lit(0)),
                                                                                                    lit(0)))

            only_querydq_final_after_join_df = only_querydq_final_after_join_df.withColumn(
                'dq_status',
                when((col('total_records') == lit(0)) & (col('valid_records') == lit(0)) & (
                        lit(source_zero_and_target_zero_is) == 'pass'), 'PASS')
                .when((lit(dq_status_calculation_attribute) == 'failed_records') & (col('failed_records') != lit(0)),
                      'PASS')
                .when((col('total_records') == '') & (col('valid_records') == ''), 'PASS')
                .when((coalesce(col('total_records'), lit(0)) == 0) & (coalesce(col('valid_records'), lit(0)) == 0),
                      'PASS')
                .when(
                    (lit(dq_status_calculation_attribute) != 'failed_records') & (
                                col('success_percentage') == lit(100.00)),
                    'PASS')
            )
            only_querydq_final_after_join_df = only_querydq_final_after_join_df.drop('total_records_only_nbr',
                                                                                     'valid_records_only_nbr')

            only_querydq_final_after_join_df = only_querydq_final_after_join_df.drop("failed_rec_perc_variance",
                                                                                     "dq_status")

            columns_to_remove = [
                "target_dq_status",
                "source_expectations",
                "source_dq_actual_outcome",
                "source_dq_expected_outcome",
                "source_dq_start_time",
                "source_dq_end_time",
                "target_expectations",
                "target_dq_actual_outcome",
                "target_dq_expected_outcome",
                "target_dq_actual_row_count",
                "target_dq_error_row_count",
                "target_dq_row_count",
                "target_dq_start_time",
                "target_dq_end_time",
                "rule_type",
                "dq_job_metadata_info",
                "tag",
                "dq_date",
                "description",
            ]
            df_stats_detailed = df_stats_detailed.drop(*columns_to_remove)

            df_stats_detailed = df_stats_detailed.withColumnRenamed("source_dq_row_count", "total_records") \
                .withColumnRenamed("source_dq_status", "status") \
                .withColumnRenamed("source_dq_actual_row_count", "valid_records") \
                .withColumnRenamed("source_dq_error_row_count", "failed_records") \
                .withColumn("success_percentage", (col("valid_records") / col("total_records")) * 100)

            df_report_table = only_querydq_final_after_join_df.unionByName(df_stats_detailed)
            df_report_table.show(truncate=False)


            return True,df_report_table
        except Exception as e:
            raise SparkExpectationsMiscException(
                f"An error occurred in dq_obs_report_data_insert: {e}"
            )



