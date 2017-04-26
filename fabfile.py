import boto3
import ConfigParser
import logging

import boto3
import time
from fabric.api import *
from fabric.contrib.files import exists

CONFIG_FILE = "settings.cfg"
config = ConfigParser.RawConfigParser()
config.read(CONFIG_FILE)

env.forward_agent = True
env.update(config._sections['ec2'])
env.hosts = [config.get('ec2', 'host')]

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - [%(levelname)s] - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

container_state = {'RUNNING': 1, 'STOPPED': 2, 'NOT_FOUND': 3}


def create_instance():
    print('creating instance')
    ec2 = boto3.resource('ec2')

    instances = ec2.create_instances(

        ImageId='ami-e1398992',
        MinCount=1,
        MaxCount=1,
        KeyName='REPLACE',
        SecurityGroupIds=['<REPLACE>'],
        InstanceType='m4.large',
        Placement={
            'AvailabilityZone': 'eu-west-1a',
        },
        BlockDeviceMappings=[
            {
                'DeviceName': '/dev/xvda',
                'Ebs': {
                    'SnapshotId': 'snap-7d042fb4',
                    'VolumeSize': 8,
                    'DeleteOnTermination': True,
                    'VolumeType': 'gp2',
                },
            },
        ],
        IamInstanceProfile={'Name': 'ec2_default_instance_role'},
        EbsOptimized=True | False
    )
    iid = instances[0].id

    # give the instance a tag name
    ec2.create_tags(
        Resources=[iid],
        Tags=mktag(env.notebook_server_tag)
    )
    return instances[0]


from fabric.colors import red, green


def assert_running(instance):
    if instance.state['Name'] != "running":

        print "Firing up instance"
        instance.start()
        # Give it 10 minutes to appear online
        for i in range(120):
            time.sleep(5)
            # instance.update()
            print instance.state
            if instance.state['Name'] == "running":
                break
        else:
            print red("Instance did not enter 'running' state within 120s.")

    if instance.state['Name'] == "running":
        dns = instance.public_dns_name
        print "Instance up and running at %s" % dns

        config.set('ec2', 'host', dns)
        config.set('ec2', 'instance', instance.id)
        # config.write(CONFIG_FILE)
        print "updating env.hosts"
        env.hosts = [dns, ]
        print env.hosts
        # Writing our configuration file to 'example.cfg'
        with open(CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    return instance


def mktag(val):
    return [{'Key': 'Name', 'Value': val}]


def assert_instance():
    """
    Return an EC2 Instance
    :return:
    """
    ec2 = boto3.resource('ec2')
    instances = ec2.instances.filter(
        Filters=[{'Name': 'tag:Name', 'Values': [env.notebook_server_tag]},
                 # {'Name': 'instance-state-name', 'Values': ['running']}
                 ])
    instance_list = [instance for instance in instances]
    if len(instance_list) == 0:
        print('not existing, will create')
        return create_instance()
    else:
        return assert_running(instance_list[0])


def initial_deployment_with_assert():
    print('checking instance')
    instance = assert_instance()
    execute(_initial_deployment, hosts=[instance.public_dns_name])


def initial_deployment():
    execute(_initial_deployment)


def _initial_deployment():
    print env.hosts
    with settings(warn_only=True):
        result = run('docker info')
        if result.failed:
            sudo('yum install -y docker')
            sudo('sudo service docker start')
            sudo('sudo usermod -a -G docker ec2-user')

    # sudo('yum install -y git')
    if not exists('bbc_tool', verbose=True):
        sudo('yum install -y git')
        run('git clone <REPLACE git repo>')
    else:
        update()

    build_container()
    start_nb_server()


def update():
    with cd('<REPLACE git_repo_dir>'):
        run('git pull')


def start_nb_server(with_assert=False):

    if with_assert:
        print('checking instance')
        instance = assert_instance()
        execute(_run_container, hosts=[instance.public_dns_name])
    else:
        execute(_run_container)


def _run_container():
    update()
    cmd = 'docker run -d -p 8888:8888 --name nb-server -v $(pwd):/opt/app dschien/ads_nb ' % \
          env.nb_password
    with cd('<REPLACE git_repo_dir>'):
        run(cmd)


def build_container(with_assert=False):
    print('checking instance')
    if with_assert:
        assert_instance()
    # with cd('bbc_tool/docker'):
    run('docker build -t dschien/ads_nb .')


def inspect_container(container_name_or_id=''):
    """ e.g. fab --host ep.iodicus.net inspect_container:container_name_or_id=... """
    with settings(warn_only=True):
        result = run("docker inspect --format '{{ .State.Running }}' " + container_name_or_id)
        running = (result == 'true')
    if result.failed:
        logger.warn('inspect_container failed for container {}'.format(container_name_or_id))
        return container_state['NOT_FOUND']
    if not running:
        logger.info('container {} stopped'.format(container_name_or_id))
        return container_state['STOPPED']
    logger.info('container {} running'.format(container_name_or_id))
    return container_state['RUNNING']


def stop_container(container_name_or_id=''):
    with settings(warn_only=True):
        result = run("docker stop " + container_name_or_id)
        if not result.failed:
            logger.info('container {} stopped'.format(container_name_or_id))


def remove_container(container_name_or_id=''):
    with settings(warn_only=True):
        result = run("docker rm " + container_name_or_id)
        if result == container_name_or_id:
            logger.info('container {} removed'.format(container_name_or_id))
        else:
            logger.warn('unexpect command result, check log output')


def docker_logs(container_name_or_id=''):
    with settings(warn_only=True):
        run('docker logs --tail 50 -f {}'.format(container_name_or_id))


def redeploy_container(container_name_or_id=''):
    """ e.g. fab --host ep.iodicus.net inspect_container:container_name_or_id=... """
    state = inspect_container(container_name_or_id)
    if state == container_state['RUNNING']:
        stop_container(container_name_or_id)
    remove_container(container_name_or_id)
    start_nb_server()


def update_site():
    """
    Pull from git and restart docker containers
    :return:
    """
    update()

    for container in ['nb-server']:
        redeploy_container(container)
