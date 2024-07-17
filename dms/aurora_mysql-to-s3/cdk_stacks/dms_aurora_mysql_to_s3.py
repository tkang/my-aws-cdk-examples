#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# vim: tabstop=2 shiftwidth=2 softtabstop=2 expandtab

import json

import aws_cdk as cdk

from aws_cdk import (
  Stack,
  aws_ec2,
  aws_dms,
  aws_iam,
  aws_secretsmanager
)
from constructs import Construct


class DMSAuroraMysqlToS3Stack(Stack):

  def __init__(self, scope: Construct, construct_id: str, vpc, **kwargs) -> None:
    super().__init__(scope, construct_id, **kwargs)

    database_name = self.node.try_get_context('source_database_name')
    table_name = self.node.try_get_context('source_table_name')

    s3_bucket_name = self.node.try_get_context('target_s3_bucket_name')
    s3_bucket_folder_name = self.node.try_get_context('target_s3_bucket_folder_name')

    db_client_sg_name = self.node.try_get_context('mysql_client_security_group_name')
    db_client_sg = aws_ec2.SecurityGroup.from_lookup_by_name(self, 'MySQLClientSG', db_client_sg_name, vpc)

    dms_replication_subnet_group = aws_dms.CfnReplicationSubnetGroup(self, 'DMSReplicationSubnetGroup',
      replication_subnet_group_description='DMS Replication Subnet Group',
      subnet_ids=vpc.select_subnets(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS).subnet_ids
    )

    dms_replication_instance = aws_dms.CfnReplicationInstance(self, 'DMSReplicationInstance',
      replication_instance_class='dms.t3.medium',
      allocated_storage=50,
      allow_major_version_upgrade=False,
      auto_minor_version_upgrade=False,
      engine_version='3.4.6',
      multi_az=False,
      preferred_maintenance_window='sat:03:17-sat:03:47',
      publicly_accessible=False,
      replication_subnet_group_identifier=dms_replication_subnet_group.ref,
      vpc_security_group_ids=[db_client_sg.security_group_id]
    )

    #XXX: If you use `aws_cdk.SecretValue.unsafe_unwrap()` to get any secret value,
    # you may probably encounter ValueError; for example, invalid literal for int() with base 10: '${Token[TOKEN.228]}'
    # So you should need to make the API call in order to access a secret inside it.
    secret_name = self.node.try_get_context('source_database_secret_name')
    secret = aws_secretsmanager.Secret.from_secret_name_v2(self, 'MySQLAdminUserSecret',
      secret_name)

    source_endpoint_id = secret.secret_value_from_json("dbClusterIdentifier").unsafe_unwrap()
    dms_source_endpoint = aws_dms.CfnEndpoint(self, 'DMSSourceEndpoint',
      endpoint_identifier=source_endpoint_id,
      endpoint_type='source',
      engine_name=secret.secret_value_from_json("engine").unsafe_unwrap(),
      server_name=secret.secret_value_from_json("host").unsafe_unwrap(),
      port=3306,
      database_name=database_name,
      username=secret.secret_value_from_json("username").unsafe_unwrap(),
      password=secret.secret_value_from_json("password").unsafe_unwrap()
    )

    dms_s3_access_role_policy_doc = aws_iam.PolicyDocument()
    dms_s3_access_role_policy_doc.add_statements(aws_iam.PolicyStatement(**{
      "effect": aws_iam.Effect.ALLOW,
      "resources": ["*"],
      "actions": [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:PutObjectTagging",
        "s3:ListBucket"]
    }))

    dms_target_s3_access_role = aws_iam.Role(self, 'DMSTargetS3AccessRole',
      role_name='DMSTargetS3AccessRole',
      assumed_by=aws_iam.ServicePrincipal('dms.amazonaws.com'),
      inline_policies={
        'S3AccessRole': dms_s3_access_role_policy_doc
      }
    )

    target_endpoint_id = f"{source_endpoint_id}-to-s3"
    dms_target_endpoint = aws_dms.CfnEndpoint(self, 'DMSTargetEndpoint',
      endpoint_identifier=target_endpoint_id,
      endpoint_type='target',
      engine_name='s3',
      s3_settings=aws_dms.CfnEndpoint.S3SettingsProperty(
        bucket_name=s3_bucket_name,
        bucket_folder=s3_bucket_folder_name,
        service_access_role_arn=dms_target_s3_access_role.role_arn,
        data_format='parquet',
        parquet_timestamp_in_millisecond=True
      )
    )

    table_mappings_json = {
      "rules": [
        {
          "rule-type": "selection",
          "rule-id": "1",
          "rule-name": "1",
          "object-locator": {
            "schema-name": database_name,
            "table-name": table_name
          },
          "rule-action": "include",
          "filters": []
        }
      ]
    }

    #XXX: AWS DMS - Using Amazon Kinesis Data Streams as a target for AWS Database Migration Service
    # https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Target.Kinesis.html
    task_settings_json = {
      # Multithreaded full load task settings
      "FullLoadSettings": {
        "MaxFullLoadSubTasks": 8,
      }
    }

    dms_replication_task = aws_dms.CfnReplicationTask(self, 'DMSReplicationTask',
      replication_task_identifier='DMSMySQLToS3Task',
      replication_instance_arn=dms_replication_instance.ref,
      migration_type='full-load', # [ full-load | cdc | full-load-and-cdc ]
      source_endpoint_arn=dms_source_endpoint.ref,
      target_endpoint_arn=dms_target_endpoint.ref,
      table_mappings=json.dumps(table_mappings_json),
      replication_task_settings=json.dumps(task_settings_json)
    )


    cdk.CfnOutput(self, 'DMSReplicationTaskArn',
      value=dms_replication_task.ref,
      export_name=f'{self.stack_name}-DMSReplicationTaskArn')
    cdk.CfnOutput(self, 'DMSReplicationTaskId',
      value=dms_replication_task.replication_task_identifier,
      export_name=f'{self.stack_name}-DMSReplicationTaskId')
    cdk.CfnOutput(self, 'DMSSourceEndpointId',
      value=dms_source_endpoint.endpoint_identifier,
      export_name=f'{self.stack_name}-DMSSourceEndpointId')
    cdk.CfnOutput(self, 'DMSTargetEndpointId',
      value=dms_target_endpoint.endpoint_identifier,
      export_name=f'{self.stack_name}-DMSTargetEndpointId')
