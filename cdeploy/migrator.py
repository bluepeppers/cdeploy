import os
import sys
import logging
import yaml
from cassandra.cluster import Cluster
from .cqlexecutor import CQLExecutor

logger = logging.getLogger(__name__)


class Migrator(object):
    def __init__(self, migrations_path, session):
        logger.info('Reading migrations from {0}'.format(migrations_path))
        self.migrations_path = migrations_path
        self.session = session

    def run_migrations(self):
        CQLExecutor.init_table(self.session)

        top_version = self.get_top_version()
        new_migration_filter = \
            lambda f: os.path.isfile(os.path.join(self.migrations_path, f)) and self.migration_version(f) > top_version
        new_migrations = self.filter_migrations(new_migration_filter)

        [self.apply_migration(file_name) for file_name in new_migrations]

    def undo(self):
        top_version = self.get_top_version()
        if top_version == 0:
            return

        top_version_filter = \
            lambda f: os.path.isfile(os.path.join(self.migrations_path, f)) and self.migration_version(f) == top_version
        top_migration = self.filter_migrations(top_version_filter)[0]

        CQLExecutor.execute_undo(self.session, self.read_migration(top_migration))
        CQLExecutor.rollback_schema_migration(self.session)
        logger.info('  -> Migration {0} undone ({1})\n'.format(top_version, top_migration))

    def get_top_version(self):
        result = CQLExecutor.get_top_version(self.session)
        top_version = result[0].version if len(result) > 0 else 0
        logger.info('Current version is {0}'.format(top_version))
        return top_version

    def filter_migrations(self, filter_func):
        migration_dir_listing = sorted(os.listdir(self.migrations_path),
                                       key=self.migration_version)
        return filter(
            filter_func,
            migration_dir_listing)

    def migration_version(self, file_name):
        return int(file_name.split('_')[0])

    def apply_migration(self, file_name):
        migration_script = self.read_migration(file_name)
        version = self.migration_version(file_name)

        CQLExecutor.execute(self.session, migration_script)
        CQLExecutor.add_schema_migration(self.session, version)
        logger.info('  -> Migration {0} applied ({1})\n'.format(version, file_name))

    def read_migration(self, file_name):
        migration_file = open(os.path.join(self.migrations_path, file_name))
        return migration_file.read()


DEFAULT_MIGRATIONS_PATH = './migrations'
CONFIG_FILE_PATH = 'config/cassandra.yml'


def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        print('Usage: cdeploy [path/to/migrations] [--undo]')
        return

    undo = False
    if '--undo' in sys.argv:
        undo = True
        sys.argv.remove('--undo')

    migrations_path = DEFAULT_MIGRATIONS_PATH if len(sys.argv) == 1 else sys.argv[1]

    if invalid_migrations_dir(migrations_path) or missing_config(migrations_path):
        return

    config = load_config(migrations_path, os.getenv('ENV'))
    cluster = Cluster(config['hosts'])
    session = cluster.connect(config['keyspace'])

    migrator = Migrator(migrations_path, session)

    if undo:
        migrator.undo()
    else:
        migrator.run_migrations()


def invalid_migrations_dir(migrations_path):
    if not os.path.isdir(migrations_path):
        logger.error('"{0}" is not a directory'.format(migrations_path))
        return True
    else:
        return False


def missing_config(migrations_path):
    config_path = config_file_path(migrations_path)
    if not os.path.exists(os.path.join(config_path)):
        logger.info('Missing configuration file "{0}"'.format(config_path))
        return True
    else:
        return False


def config_file_path(migrations_path):
    return os.path.join(migrations_path, CONFIG_FILE_PATH)


def load_config(migrations_path, env):
    config_file = open(config_file_path(migrations_path))
    config = yaml.load(config_file)
    return config[env or 'development']


if __name__ == '__main__':
    main()
