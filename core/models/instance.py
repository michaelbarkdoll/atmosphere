"""
  Instance model for atmosphere.
"""
import pytz
from hashlib import md5
from datetime import datetime

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from threepio import logger

from core.models.machine import ProviderMachine, convertEshMachine
from core.models.tag import Tag



class Instance(models.Model):
    """
    When a user launches a machine, an Instance is created.
    Instances are described by their Name and associated Tags
    Instances have a specific ID
    of the machine they were created from (Provider Machine)
    Instances have a specific ID of their own (Provider Alias)
    The IP Address, creation and termination date,
    and the user who launched the instance are recorded for logging purposes.
    """
    esh = None
    name = models.CharField(max_length=256)
    #TODO: Create custom Uuidfield?
    #token = Used for looking up the instance on deployment
    token = models.CharField(max_length=36, blank=True, null=True)
    tags = models.ManyToManyField(Tag)
    # The specific machine & provider for which this instance exists
    provider_machine = models.ForeignKey(ProviderMachine)
    provider_alias = models.CharField(max_length=256, unique=True)
    ip_address = models.GenericIPAddressField(null=True, unpack_ipv4=True)
    created_by = models.ForeignKey(User)
    # do not set a default value for start_date
    # it creates more problems than it solves; trust me.
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True)
    shell = models.BooleanField()
    vnc = models.BooleanField()

    def creator_name(self):
        return self.created_by.username

    def hash_alias(self):
        return md5(self.provider_alias).hexdigest()

    def hash_machine_alias(self):
        return md5(self.esh._node.extra['imageId']).hexdigest()

    def esh_status(self):
        if not self.esh:
            return "Unknown"
        status = self.esh.get_status()
        return status

    def esh_size(self):
        if not self.esh:
            return "Unknown"
        return self.esh._node.extra['instancetype']

    def esh_machine_name(self):
        if not self.esh:
            return "Unknown"
        return self.esh.machine.name

    def esh_machine(self):
        if not self.esh:
            return "Unknown"
        return self.esh._node.extra['imageId']

    def json(self):
        return {
            'alias': self.provider_alias,
            'name': self.name,
            'tags': [tag.json() for tag in self.tags.all()],
            'ip_address': self.ip_address,
            'provider_machine': self.provider_machine.json(),
            'created_by': self.created_by.username,
        }

    def __unicode__(self):
        return "%s (Name:%s, Creator:%s, IP:%s)" %\
            (self.provider_alias, self.name,
             self.created_by, self.ip_address)

    class Meta:
        db_table = "instance"
        app_label = "core"

"""
Useful utility methods for the Core Model..
"""


def findInstance(alias):
    core_instance = Instance.objects.filter(provider_alias=alias)
    if len(core_instance) > 1:
        logger.warn("Multiple instances returned for alias - %s" % alias)
    if core_instance:
        return core_instance[0]
    return None


def convertEshInstance(esh_driver, esh_instance, provider_id, user, token=None):
    """
    """
    logger.debug(esh_instance.__dict__)
    logger.debug(esh_instance.extra)
    alias = esh_instance.alias
    try:
        ip_address = esh_instance._node.public_ips[0]
    except IndexError:  # no public ip
        try:
            ip_address = esh_instance._node.private_ips[0]
        except IndexError:  # no private ip
            ip_address = '0.0.0.0'
    eshMachine = esh_instance.machine
    core_instance = findInstance(alias)
    if core_instance:
        core_instance.ip_address = ip_address
        core_instance.save()
    else:
        if 'launchdatetime' in esh_instance.extra:
            create_stamp = esh_instance.extra.get('launchdatetime')
        elif 'created' in esh_instance.extra:
            create_stamp = esh_instance.extra.get('created')
        else:
            raise Exception("Instance does not have a created timestamp.  This"
            "should never happen. Don't cheat and assume it was created just "
            "now. Get the real launch time, bra.")

        # create_stamp is an iso 8601 timestamp string that may or may not
        # include microseconds start_date is a timezone-aware datetime object
        try:
            start_date = datetime.strptime(create_stamp, '%Y-%m-%dT%H:%M:%S.%fZ')
        except ValueError:
            start_date = datetime.strptime(create_stamp, '%Y-%m-%dT%H:%M:%SZ')
        start_date = start_date.replace(tzinfo=pytz.utc)

        logger.debug("CREATED: %s" % create_stamp)
        logger.debug("START: %s" % start_date)

        coreMachine = convertEshMachine(esh_driver, eshMachine, provider_id)
        core_instance = createInstance(provider_id, alias,
                                      coreMachine, ip_address,
                                      esh_instance.name, user,
                                      start_date, token)

    core_instance.esh = esh_instance

    core_instance = set_instance_from_metadata(esh_driver, core_instance)
    return core_instance

def set_instance_from_metadata(esh_driver, core_instance):
    #Fixes Dep. loop - Do not remove
    from api.serializers import InstanceSerializer
    #Breakout for drivers (Eucalyptus) that don't support metadata
    if not hasattr(esh_driver._connection, 'ex_get_metadata'):
        #logger.debug("EshDriver %s does not have function 'ex_get_metadata'"
        #            % esh_driver._connection.__class__)
        return core_instance
    esh_instance = core_instance.esh
    metadata =  esh_driver._connection.ex_get_metadata(esh_instance)
    serializer = InstanceSerializer(core_instance, data=metadata, partial=True)
    if not serializer.is_valid():
        logger.warn("Encountered errors serializing metadata:%s"
                    % serializer.errors)
        return core_instance
    serializer.save()
    core_instance = serializer.object
    core_instance.esh = esh_instance
    return core_instance

def update_instance_metadata(esh_driver, esh_instance, data={}, replace=True):
    """
    NOTE: This will NOT WORK for TAGS until openstack
    allows JSONArrays as values for metadata!
    """
    if not hasattr(esh_driver._connection, 'ex_set_metadata'):
        logger.info("EshDriver %s does not have function 'ex_set_metadata'"
                    % esh_driver._connection.__class__)
        return {}

    if data.get('name'):
        esh_driver._connection.ex_set_server_name(esh_instance, data['name'])
    try:
        return esh_driver._connection.ex_set_metadata(esh_instance, data,
                replace_metadata=replace)
    except Exception, e:
        if 'incapable of performing the request' in e.message:
            return {}
        else:
            raise

def createInstance(provider_id, provider_alias, provider_machine,
                   ip_address, name, creator, create_stamp, token=None):
    new_inst = Instance.objects.create(name=name,
                                       provider_alias=provider_alias,
                                       provider_machine=provider_machine,
                                       ip_address=ip_address,
                                       created_by=creator,
                                       token=token,
                                       start_date=create_stamp)
    new_inst.save()
    logger.debug("New instance created - %s (Token = %s)" %
                 (provider_alias, token))
    return new_inst
