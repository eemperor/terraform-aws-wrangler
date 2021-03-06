#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render Terraform files using Jinja 2."""
# Credit for the core of this idea goes to:
#   <https://github.com/Crapworks/terratools/tree/master/terratemplate>
from __future__ import (absolute_import, division, print_function,
                        unicode_literals, with_statement)

import argparse
import ast
import glob
import hashlib
import io
import itertools
import json
import os
import re
import sys

import boto3
import hcl
import six
from jinja2 import Environment, FileSystemLoader

S3 = boto3.client("s3")
S3_PAGINATOR = S3.get_paginator('list_objects_v2')


class JinjaFilter(object):
    """Decorator to mark functions as Jinja filters."""

    filters = {}

    def __init__(self, name=None):  # noqa: D107
        self.name = name

    def __call__(self, function):  # noqa: D102
        name = self.name or function.__name__
        if name not in self.filters:
            self.filters[name] = function
        return function


jinja_filter = JinjaFilter


@jinja_filter("sha256")
def sha256(data, encoding="utf-8"):
    """Generate an sha256 hash of a given string."""
    data = data.encode(encoding) if isinstance(data, six.text_type) else data
    return hashlib.sha256(data).hexdigest()


@jinja_filter("regex_replace")
def regex_replace(value, pattern, repl):
    """Perform ``re.sub`` on provided value."""
    return re.sub(pattern, repl, value)


@jinja_filter("dirname")
def dirname(value):
    """Return the ``os.path.dirname`` of ``value``."""
    return os.path.dirname(value)


def keys_from_bucket_objects(objects):
    """Extract keys from a list of S3 bucket objects."""
    return [x["Key"] for x in objects if not x["Key"].endswith("/")]


def s3_list_objects(bucket, prefix='/', delimiter='/', start_after=''):
    """List S3 Keys."""
    prefix = prefix[1:] if prefix.startswith(delimiter) else prefix
    start_after = (
      (start_after or prefix) if prefix.endswith(delimiter) else start_after
    )
    for page in S3_PAGINATOR.paginate(
        Bucket=bucket, Prefix=prefix, StartAfter=start_after
    ):
        for content in page.get('Contents', ()):
            yield content


@jinja_filter("s3_list_keys")
def s3_list_keys(bucket, prefixes=None):
    """List S3 Keys."""
    prefixes = prefixes if prefixes else [""]
    return itertools.chain.from_iterable([
        keys_from_bucket_objects(
            s3_list_objects(bucket, prefix=prefix)
        ) for prefix in prefixes
    ])


def load_variables(var_objects):
    """Load terraform variables."""
    variables = {}

    for terrafile in glob.glob("./*.tf"):
        with io.open(file=terrafile) as fh:
            data = hcl.load(fh)
            for key, value in data.get("variable", {}).items():
                env_key = '{0}{1}'.format('TF_VAR_', key)
                if env_key in os.environ:
                    val = os.environ[env_key]
                    try:
                        variables.update(hcl.loads('{0} = {1}'.format(
                            key, val)))
                    except ValueError:
                        # Naively wrap value in double quotes
                        # TODO: test more variations, extract string handling
                        #       to dedicated function
                        variables.update(hcl.loads('{0} = "{1}"'.format(
                            key, val)))
                elif "default" in value:
                    variables.update({key: value["default"]})

    for var in var_objects:
        data = var
        if os.path.isfile(path=data):
            with io.open(file=var) as fh:
                data = fh.read()
        else:
            try:
                # Convert to json a string that looks like a python object
                data = json.dumps(ast.literal_eval(data))
            except (SyntaxError, ValueError):
                pass

        variables.update(hcl.loads(data))

    return variables


def render(template, context):
    """Render Jinja templates."""
    path, filename = os.path.split(template)
    env = Environment(
        loader=FileSystemLoader(path or "./"),
        extensions=["jinja2.ext.do"]
    )
    env.filters.update(JinjaFilter.filters)
    return env.get_template(filename).render(context)


def main(args):
    """Process files."""
    context = load_variables(args.vars)
    if args.show_vars:
        print(json.dumps(context, indent=4))

    for template in glob.glob("{}/*.jinja".format(args.PATH)):
        rendered_filename = "{}.tf".format(os.path.splitext(template)[0])
        rendered_contents = render(template, context)
        if args.test:
            print(rendered_contents)
        else:
            with io.open(file=rendered_filename, mode="w") as fh:
                fh.write(rendered_contents)


def cli():
    """Load args."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-var-file", dest="vars", action="append", default=[])
    parser.add_argument(
        "-var", dest="vars", action="append", default=[])
    parser.add_argument("-t", "--test", action="store_true")
    parser.add_argument("-s", "--show-vars", action="store_true")
    parser.add_argument("PATH", nargs="?", default=".")
    args = parser.parse_args()

    main(args)


if __name__ == "__main__":
    sys.exit(cli())
