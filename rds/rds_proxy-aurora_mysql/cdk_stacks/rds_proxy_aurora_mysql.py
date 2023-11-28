#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# vim: tabstop=2 shiftwidth=2 softtabstop=2 expandtab

import json

import aws_cdk as cdk

from aws_cdk import (
  Stack,
  aws_ec2,
  aws_logs,
  aws_rds,
  aws_secretsmanager
)
from constructs import Construct


class RdsProyAuroraMysqlStack(Stack):

  def __init__(self, scope: Construct, id: str, vpc, **kwargs) -> None:
    super().__init__(scope, id, **kwargs)

    db_cluster_name = self.node.try_get_context('db_cluster_name')

    sg_mysql_client = aws_ec2.SecurityGroup(self, 'MySQLClientSG',
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for mysql client',
      security_group_name=f'mysql-client-sg-{db_cluster_name}'
    )
    cdk.Tags.of(sg_mysql_client).add('Name', 'mysql-client-sg')

    sg_mysql_server = aws_ec2.SecurityGroup(self, 'MySQLServerSG',
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for mysql',
      security_group_name=f'mysql-server-sg-{db_cluster_name}'
    )
    sg_mysql_server.add_ingress_rule(peer=sg_mysql_client, connection=aws_ec2.Port.tcp(3306),
      description='mysq-client-sg')
    sg_mysql_server.add_ingress_rule(peer=sg_mysql_server, connection=aws_ec2.Port.all_tcp(),
      description='mysql-server-sg')
    cdk.Tags.of(sg_mysql_server).add('Name', 'mysql-server-sg')

    rds_subnet_group = aws_rds.SubnetGroup(self, 'RdsSubnetGroup',
      description='subnet group for mysql',
      subnet_group_name=f'aurora-mysql-sbunet-{db_cluster_name}',
      vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS),
      vpc=vpc
    )

    # rds_engine = aws_rds.DatabaseClusterEngine.aurora_mysql(version=aws_rds.AuroraMysqlEngineVersion.VER_2_08_1)
    rds_engine = aws_rds.DatabaseClusterEngine.aurora_mysql(version=aws_rds.AuroraMysqlEngineVersion.VER_3_01_0)

    rds_cluster_param_group = aws_rds.ParameterGroup(self, 'AuroraMySQLClusterParamGroup',
      engine=rds_engine,
      description='Custom cluster parameter group for aurora-mysql8.x',
      parameters={
        # For Aurora MySQL version 3, Aurora always uses the default value of 1.
        # 'innodb_flush_log_at_trx_commit': '2',
        'slow_query_log': '1',
        # Removed from Aurora MySQL version 3.
        # 'tx_isolation': 'READ-COMMITTED',
        'wait_timeout': '300',
        'character-set-client-handshake': '0',
        'character_set_server': 'utf8mb4',
        'collation_server': 'utf8mb4_unicode_ci',
        'init_connect': 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci',
        #'binlog_format': 'ROW' #XXX: Turn on binlog
      }
    )

    rds_db_param_group = aws_rds.ParameterGroup(self, 'AuroraMySQLDBParamGroup',
      engine=rds_engine,
      description='Custom parameter group for aurora-mysql8.x',
      parameters={
        'slow_query_log': '1',
        # Removed from Aurora MySQL version 3.
        # 'tx_isolation': 'READ-COMMITTED',
        'wait_timeout': '300',
        'init_connect': 'SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci'
      }
    )

    #XXX: In order to exclude punctuations when generating a password
    # use aws_secretsmanager.Secret instead of aws_rds.DatabaseSecret.
    # Othwerise, an error occurred such as:
    # "All characters of the desired type have been excluded"
    db_secret = aws_secretsmanager.Secret(self, 'DatabaseSecret',
      generate_secret_string=aws_secretsmanager.SecretStringGenerator(
        secret_string_template=json.dumps({"username": "admin"}),
        generate_string_key="password",
        exclude_punctuation=True,
        password_length=8
      )
    )
    rds_credentials = aws_rds.Credentials.from_secret(db_secret)

    db_cluster = aws_rds.DatabaseCluster(self, 'Database',
      engine=rds_engine,
      credentials=rds_credentials,
      writer=aws_rds.ClusterInstance.provisioned("writer",
        instance_type=aws_ec2.InstanceType.of(aws_ec2.InstanceClass.BURSTABLE3, aws_ec2.InstanceSize.MEDIUM),
        parameter_group=rds_db_param_group,
        auto_minor_version_upgrade=False,
      ),
      readers=[
        aws_rds.ClusterInstance.provisioned("reader",
          instance_type=aws_ec2.InstanceType.of(aws_ec2.InstanceClass.BURSTABLE3, aws_ec2.InstanceSize.MEDIUM),
          parameter_group=rds_db_param_group,
          auto_minor_version_upgrade=False
        )
      ],
      parameter_group=rds_cluster_param_group,
      cloudwatch_logs_retention=aws_logs.RetentionDays.THREE_DAYS,
      cluster_identifier=db_cluster_name,
      subnet_group=rds_subnet_group,
      backup=aws_rds.BackupProps(
        retention=cdk.Duration.days(3),
        preferred_window="03:00-04:00"
      ),
      security_groups=[sg_mysql_server],
      vpc=vpc,
      vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS)
    )

    sg_mysql_public_proxy = aws_ec2.SecurityGroup(self, 'MySQLPublicProxySG',
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for mysql public proxy',
      security_group_name=f'mysql-public-proxy-sg-{db_cluster_name}'
    )
    sg_mysql_public_proxy.add_ingress_rule(peer=aws_ec2.Peer.any_ipv4(), connection=aws_ec2.Port.tcp(3306), description='mysql public proxy')
    cdk.Tags.of(sg_mysql_public_proxy).add('Name', 'mysql-public-proxy-sg')

    #XXX: Datbase Proxy use only Secret Arn of target database or database cluster
    #XXX: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-rds-dbproxy-authformat.html
    #XXX: If new Secret for database user is created, it is necessary to update Resource of Proxy IAM Role to access new Secret.
    #XXX: Otherwise, new database user can not connect to database by RDS Proxy.
    db_proxy = aws_rds.DatabaseProxy(self, 'DBProxy',
      proxy_target=aws_rds.ProxyTarget.from_cluster(db_cluster),
      secrets=[db_cluster.secret],
      vpc=vpc,
      db_proxy_name=f'{db_cluster_name}-proxy',
      idle_client_timeout=cdk.Duration.minutes(10),
      max_connections_percent=90,
      require_tls=False, #XXX: disable the setting Require Transport Layer Security in the proxy
      max_idle_connections_percent=10,
      security_groups=[sg_mysql_client, sg_mysql_public_proxy],
      vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PUBLIC)
    )
    db_proxy.node.add_dependency(db_cluster)

    db_proxy_readonly_endpoint = aws_rds.CfnDBProxyEndpoint(self, 'RDSProxyReadOnlyEndpoint',
      db_proxy_endpoint_name=f'{db_cluster_name}-proxy-readonly',
      db_proxy_name=f'{db_cluster_name}-proxy',
      vpc_subnet_ids=vpc.select_subnets(subnet_type=aws_ec2.SubnetType.PUBLIC).subnet_ids,
      target_role='READ_ONLY',
      vpc_security_group_ids=[sg_mysql_client.security_group_id, sg_mysql_public_proxy.security_group_id]
    )
    db_proxy_readonly_endpoint.node.add_dependency(db_proxy)


    cdk.CfnOutput(self, 'DBProxyName', value=db_proxy.db_proxy_name,
      export_name=f'{self.stack_name}-DBProxyName')
    cdk.CfnOutput(self, 'DBProxyEndpoint', value=db_proxy.endpoint,
      export_name=f'{self.stack_name}-DBProxyEndpoint')
    cdk.CfnOutput(self, 'DBProxyReadOnlyEndpoint', value=db_proxy_readonly_endpoint.attr_endpoint,
      export_name=f'{self.stack_name}-DBProxyReadOnlyEndpoint')
    cdk.CfnOutput(self, 'DBClusterEndpoint', value=db_cluster.cluster_endpoint.socket_address,
      export_name=f'{self.stack_name}-DBClusterEndpoint')
    cdk.CfnOutput(self, 'DBClusterReadEndpoint', value=db_cluster.cluster_read_endpoint.socket_address,
      export_name=f'{self.stack_name}-DBClusterReadEndpoint')
    cdk.CfnOutput(self, 'RDSClientSecurityGroupId', value=sg_mysql_client.security_group_id,
      export_name=f'{self.stack_name}-RDSClientSecurityGroupId')
    cdk.CfnOutput(self, 'DBSecretName', value=db_cluster.secret.secret_name,
      export_name=f'{self.stack_name}-DBSecretName')