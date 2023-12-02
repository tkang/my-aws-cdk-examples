#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# vim: tabstop=2 shiftwidth=2 softtabstop=2 expandtab

import os

from cdk_stacks import (
  KDAFlinkMskReplicationStack
)

import aws_cdk as cdk


APP_ENV = cdk.Environment(
  account=os.getenv('CDK_DEFAULT_ACCOUNT'),
  region=os.getenv('CDK_DEFAULT_REGION')
)

app = cdk.App()

msk_replication_stack = KDAFlinkMskReplicationStack(app, "KDAFlinkMskReplicationStack",
  env=APP_ENV
)

app.synth()
