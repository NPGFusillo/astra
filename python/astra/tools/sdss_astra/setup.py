from __future__ import absolute_import, division, print_function, unicode_literals

import click
import os
from shutil import rmtree
from astra import log
from astra.db.connection import Base, engine
from sqlalchemy_utils import database_exists, create_database

@click.command()
@click.option("-y", "confirm", default=False, is_flag=True,
              help="drop the database if it already exists")
@click.pass_context
def setup(context, confirm):
    r""" Setup databases using the current configuration. """

    log.debug("Running setup")

    if not database_exists(engine.url):
        log.info(f"Creating database {engine.url}")
        create_database(engine.url)

    elif not confirm \
         and click.confirm("Database already exists. This will wipe the database, including all "\
                           "downloaded components, and start again. Are you sure?", abort=True):
        None

    log.debug("Dropping all tables")
    Base.metadata.drop_all(engine)

    log.debug("Creating all tables")
    Base.metadata.create_all(engine)

    log.debug("Removing old components")
    component_dir = os.getenv("ASTRA_COMPONENT_DIR", None)
    if component_dir is not None:
        if os.path.exists(component_dir):
            rmtree(component_dir)
        os.makedirs(component_dir, exist_ok=True)


    log.info("Astra is ready.")
    return None