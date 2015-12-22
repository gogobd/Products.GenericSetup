##############################################################################
#
# Copyright (c) 2004 Zope Foundation and Contributors.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
""" Classes:  ImportStepRegistry, ExportStepRegistry
"""

from xml.sax import parseString
from xml.sax.handler import ContentHandler

from AccessControl.SecurityInfo import ClassSecurityInfo
from Acquisition import Implicit
from App.class_init import InitializeClass
from Products.PageTemplates.PageTemplateFile import PageTemplateFile
from zope.interface import implements
from zope.component import getGlobalSiteManager

from Products.GenericSetup.interfaces import BASE
from Products.GenericSetup.interfaces import IImportStepRegistry
from Products.GenericSetup.interfaces import IExportStepRegistry
from Products.GenericSetup.interfaces import IToolsetRegistry
from Products.GenericSetup.interfaces import IProfileRegistry
from Products.GenericSetup.interfaces import IProfile
from Products.GenericSetup.interfaces import IImportStep
from Products.GenericSetup.interfaces import IExportStep
from Products.GenericSetup.permissions import ManagePortal
from Products.GenericSetup.metadata import ProfileMetadata
from Products.GenericSetup.utils import _xmldir
from Products.GenericSetup.utils import _getDottedName
from Products.GenericSetup.utils import _resolveDottedName
from Products.GenericSetup.utils import _extractDocstring
from Products.GenericSetup.utils import _computeTopologicalSort

#
#   XML parser
#


class _HandlerBase(ContentHandler):

    _MARKER = object()

    def _extract(self, attrs, key):
        result = attrs.get(key, self._MARKER)

        if result is self._MARKER:
            return None

        return self._encode(result)

    def _encode(self, content):
        if self._encoding is None:
            return content

        return content.encode(self._encoding)


class _ToolsetParser(_HandlerBase):

    security = ClassSecurityInfo()
    security.declareObjectPrivate()
    security.setDefaultAccess('deny')

    def __init__(self, encoding):
        self._encoding = encoding
        self._required = {}
        self._forbidden = []

    def startElement(self, name, attrs):
        if name == 'tool-setup':
            pass

        elif name == 'forbidden':
            tool_id = self._extract(attrs, 'tool_id')

            if tool_id not in self._forbidden:
                self._forbidden.append(tool_id)

        elif name == 'required':
            tool_id = self._extract(attrs, 'tool_id')
            dotted_name = self._extract(attrs, 'class')
            self._required[tool_id] = dotted_name

        else:
            raise ValueError('Unknown element %s' % name)

InitializeClass(_ToolsetParser)


class _ImportStepRegistryParser(_HandlerBase):

    security = ClassSecurityInfo()
    security.declareObjectPrivate()
    security.setDefaultAccess('deny')

    def __init__(self, encoding):
        self._encoding = encoding
        self._started = False
        self._pending = None
        self._parsed = []

    def startElement(self, name, attrs):
        if name == 'import-steps':
            if self._started:
                raise ValueError('Duplicated setup-steps element: %s' % name)

            self._started = True

        elif name == 'import-step':
            if self._pending is not None:
                raise ValueError('Cannot nest setup-step elements')

            self._pending = dict([ (k, self._extract(attrs, k))
                                   for k in attrs.keys() ])

            self._pending['dependencies'] = []

        elif name == 'dependency':
            if not self._pending:
                raise ValueError('Dependency outside of step')

            depended = self._extract(attrs, 'step')
            self._pending['dependencies'].append(depended)

        else:
            raise ValueError('Unknown element %s' % name)

    def characters(self, content):
        if self._pending is not None:
            content = self._encode(content)
            self._pending.setdefault('description', []).append(content)

    def endElement(self, name):
        if name == 'import-steps':
            pass

        elif name == 'import-step':
            if self._pending is None:
                raise ValueError('No pending step!')

            deps = tuple(self._pending['dependencies'])
            self._pending['dependencies'] = deps

            desc = ''.join(self._pending['description'])
            self._pending['description'] = desc

            self._parsed.append(self._pending)
            self._pending = None

InitializeClass(_ImportStepRegistryParser)


class _ExportStepRegistryParser(_HandlerBase):

    security = ClassSecurityInfo()
    security.declareObjectPrivate()
    security.setDefaultAccess('deny')

    def __init__(self, encoding):
        self._encoding = encoding
        self._started = False
        self._pending = None
        self._parsed = []

    def startElement(self, name, attrs):
        if name == 'export-steps':
            if self._started:
                raise ValueError('Duplicated export-steps element: %s' % name)

            self._started = True

        elif name == 'export-step':
            if self._pending is not None:
                raise ValueError('Cannot nest export-step elements')

            self._pending = dict([ (k, self._extract(attrs, k))
                                   for k in attrs.keys() ])

        else:
            raise ValueError('Unknown element %s' % name)

    def characters(self, content):
        if self._pending is not None:
            content = self._encode(content)
            self._pending.setdefault('description', []).append(content)

    def endElement(self, name):
        if name == 'export-steps':
            pass

        elif name == 'export-step':
            if self._pending is None:
                raise ValueError('No pending step!')

            desc = ''.join(self._pending['description'])
            self._pending['description'] = desc

            self._parsed.append(self._pending)
            self._pending = None

InitializeClass(_ExportStepRegistryParser)


class GlobalRegistryStorage(object):

    def __init__(self, interfaceClass):
        self.interfaceClass = interfaceClass

    def keys(self):
        sm = getGlobalSiteManager()
        return [ n for n, _i in sm.getUtilitiesFor(self.interfaceClass) ]

    def values(self):
        sm = getGlobalSiteManager()
        return [ i for _n, i in sm.getUtilitiesFor(self.interfaceClass) ]

    def get(self, key):
        sm = getGlobalSiteManager()
        return sm.queryUtility(provided=self.interfaceClass, name=key)

    def __setitem__(self, id, info):
        sm = getGlobalSiteManager()
        return sm.registerUtility(info, provided=self.interfaceClass, name=id)

    def __delitem__(self, id):
        sm = getGlobalSiteManager()
        return sm.unregisterUtility(provided=self.interfaceClass, name=id)

    def clear(self):
        for key in self.keys():
            del self[key]


class BaseStepRegistry(Implicit):

    security = ClassSecurityInfo()

    def __init__(self, store=None):
        if store is None:
            store = {}
        self._registered = store
        self.clear()

    security.declareProtected(ManagePortal, 'listSteps')
    def listSteps(self):
        """ Return a list of registered step IDs.
        """
        return self._registered.keys()

    security.declareProtected(ManagePortal, 'getStepMetadata')
    def getStepMetadata(self, key, default=None):
        """ Return a mapping of metadata for the step identified by 'key'.

        o Return 'default' if no such step is registered.

        o The 'handler' metadata is available via 'getStep'.
        """
        info = self._registered.get(key)

        if info is None:
            return default

        result = info.copy()
        result['invalid'] = _resolveDottedName(result['handler']) is None

        return result

    security.declareProtected(ManagePortal, 'listStepMetadata')
    def listStepMetadata(self):
        """ Return a sequence of mappings describing registered steps.

        o Mappings will be ordered alphabetically.
        """
        step_ids = self.listSteps()
        step_ids.sort()
        return [ self.getStepMetadata(x) for x in step_ids ]

    security.declareProtected(ManagePortal, 'generateXML')
    def generateXML(self, encoding='utf-8'):
        """ Return a round-trippable XML representation of the registry.

        o 'handler' values are serialized using their dotted names.
        """
        return self._exportTemplate().encode('utf-8')

    security.declarePrivate('getStep')
    def getStep(self, key, default=None):
        """ Return the I(Export|Import)Plugin registered for 'key'.

        o Return 'default' if no such step is registered.
        """
        info = self._registered.get(key)

        if info is None:
            return default

        return _resolveDottedName(info['handler'])

    security.declarePrivate('unregisterStep')
    def unregisterStep(self, id):
        del self._registered[id]

    security.declarePrivate('clear')
    def clear(self):
        self._registered.clear()

    security.declarePrivate('parseXML')
    def parseXML(self, text, encoding='utf-8'):
        """ Parse 'text'.
        """
        reader = getattr(text, 'read', None)

        if reader is not None:
            text = reader()

        parser = self.RegistryParser(encoding)
        parseString(text, parser)

        return parser._parsed

InitializeClass(BaseStepRegistry)


class ImportStepRegistry(BaseStepRegistry):

    """ Manage knowledge about steps to create / configure site.

    o Steps are composed together to define a site profile.
    """
    implements(IImportStepRegistry)

    security = ClassSecurityInfo()
    RegistryParser = _ImportStepRegistryParser

    security.declareProtected(ManagePortal, 'sortSteps')
    def sortSteps(self):
        """ Return a sequence of registered step IDs

        o Sequence is sorted topologically by dependency, with the dependent
          steps *after* the steps they depend on.
        """
        return self._computeTopologicalSort()

    security.declareProtected(ManagePortal, 'checkComplete')
    def checkComplete(self):
        """ Return a sequence of ( node, edge ) tuples for unsatisifed deps.
        """
        result = []
        seen = {}

        graph = self._computeTopologicalSort()

        for node in graph:

            dependencies = self.getStepMetadata(node)['dependencies']

            for dependency in dependencies:

                if seen.get(dependency) is None:
                    result.append((node, dependency))

            seen[node] = 1

        return result

    security.declarePrivate('registerStep')
    def registerStep(self, id, version=None, handler=None, dependencies=(),
                     title=None, description=None):
        """ Register a setup step.

        o 'id' is a unique name for this step,

        o 'version' is a string for comparing versions, it is preferred to
          be a yyyy/mm/dd-ii formatted string (date plus two-digit
          ordinal).  when comparing two version strings, the version with
          the lower sort order is considered the older version.

          - Newer versions of a step supplant older ones.

          - Attempting to register an older one after a newer one results
            in a KeyError.

        o 'handler' is the dottoed name of a handler which should implement
           IImportPlugin.

        o 'dependencies' is a tuple of step ids which have to run before
          this step in order to be able to run at all. Registration of
          steps that have unmet dependencies are deferred until the
          dependencies have been registered.

        o 'title' is a one-line UI description for this step.
          If None, the first line of the documentation string of the handler
          is used, or the id if no docstring can be found.

        o 'description' is a one-line UI description for this step.
          If None, the remaining line of the documentation string of
          the handler is used, or default to ''.
        """
        already = self.getStepMetadata(id)

        if handler is None:
            raise ValueError('No handler specified')

        if already and already['version'] > version:
            raise KeyError('Existing registration for step %s, version %s'
                           % (id, already['version']))

        if not isinstance(handler, str):
            handler = _getDottedName(handler)

        if title is None or description is None:

            method = _resolveDottedName(handler)
            if method is None:
                t, d = id, ''
            else:
                t, d = _extractDocstring(method, id, '')

            title = title or t
            description = description or d

        info = {'id': id,
                'version': version,
                'handler': handler,
                'dependencies': dependencies,
                'title': title,
                'description': description}

        self._registered[id] = info

    #
    #   Helper methods
    #
    security.declarePrivate('_computeTopologicalSort')
    def _computeTopologicalSort(self):
        return _computeTopologicalSort(self._registered.values())

    security.declarePrivate('_exportTemplate')
    _exportTemplate = PageTemplateFile('isrExport.xml', _xmldir)

InitializeClass(ImportStepRegistry)

_import_step_registry = ImportStepRegistry(GlobalRegistryStorage(IImportStep))


class ExportStepRegistry(BaseStepRegistry):

    """ Registry of known site-configuration export steps.

    o Each step is registered with a unique id.

    o When called, with the portal object passed in as an argument,
      the step must return a sequence of three-tuples,
      ( 'data', 'content_type', 'filename' ), one for each file exported
      by the step.

      - 'data' is a string containing the file data;

      - 'content_type' is the MIME type of the data;

      - 'filename' is a suggested filename for use when downloading.

    """
    implements(IExportStepRegistry)

    security = ClassSecurityInfo()
    RegistryParser = _ExportStepRegistryParser

    security.declarePrivate('registerStep')
    def registerStep(self, id, handler, title=None, description=None):
        """ Register an export step.

        o 'id' is the unique identifier for this step

        o 'handler' is the dottoed name of a handler which should implement
           IImportPlugin.

        o 'title' is a one-line UI description for this step.
          If None, the first line of the documentation string of the step
          is used, or the id if no docstring can be found.

        o 'description' is a one-line UI description for this step.
          If None, the remaining line of the documentation string of
          the step is used, or default to ''.
        """
        if not isinstance(handler, str):
            handler = _getDottedName(handler)

        if title is None or description is None:

            method = _resolveDottedName(handler)
            if method is None:
                t, d = id, ''
            else:
                t, d = _extractDocstring(method, id, '')

            title = title or t
            description = description or d

        info = {'id': id,
                'handler': handler,
                'title': title,
                'description': description}

        self._registered[id] = info

    #
    #   Helper methods
    #
    security.declarePrivate('_exportTemplate')
    _exportTemplate = PageTemplateFile('esrExport.xml', _xmldir)

InitializeClass(ExportStepRegistry)

_export_step_registry = ExportStepRegistry(GlobalRegistryStorage(IExportStep))


class ToolsetRegistry(Implicit):

    """ Track required / forbidden tools.
    """
    implements(IToolsetRegistry)

    security = ClassSecurityInfo()
    security.setDefaultAccess('allow')

    def __init__(self):
        self.clear()

    #
    #   Toolset API
    #
    security.declareProtected(ManagePortal, 'listForbiddenTools')
    def listForbiddenTools(self):
        """ See IToolsetRegistry.
        """
        result = list(self._forbidden)
        result.sort()
        return result

    security.declareProtected(ManagePortal, 'addForbiddenTool')
    def addForbiddenTool(self, tool_id):
        """ See IToolsetRegistry.
        """
        if tool_id in self._forbidden:
            return

        if self._required.get(tool_id) is not None:
            raise ValueError('Tool %s is required!' % tool_id)

        self._forbidden.append(tool_id)

    security.declareProtected(ManagePortal, 'listRequiredTools')
    def listRequiredTools(self):
        """ See IToolsetRegistry.
        """
        result = list(self._required.keys())
        result.sort()
        return result

    security.declareProtected(ManagePortal, 'getRequiredToolInfo')
    def getRequiredToolInfo(self, tool_id):
        """ See IToolsetRegistry.
        """
        return self._required[tool_id]

    security.declareProtected(ManagePortal, 'listRequiredToolInfo')
    def listRequiredToolInfo(self):
        """ See IToolsetRegistry.
        """
        return [ self.getRequiredToolInfo(x)
                 for x in self.listRequiredTools() ]

    security.declareProtected(ManagePortal, 'addRequiredTool')
    def addRequiredTool(self, tool_id, dotted_name):
        """ See IToolsetRegistry.
        """
        if tool_id in self._forbidden:
            raise ValueError("Forbidden tool ID: %s" % tool_id)

        self._required[tool_id] = {'id': tool_id, 'class': dotted_name}

    security.declareProtected(ManagePortal, 'generateXML')
    def generateXML(self, encoding='utf-8'):
        """ Pseudo API.
        """
        return self._toolsetConfig().encode('utf-8')

    security.declareProtected(ManagePortal, 'parseXML')
    def parseXML(self, text, encoding='utf-8'):
        """ Pseudo-API
        """
        reader = getattr(text, 'read', None)

        if reader is not None:
            text = reader()

        parser = _ToolsetParser(encoding)
        parseString(text, parser)

        for tool_id in parser._forbidden:
            self.addForbiddenTool(tool_id)

        for tool_id, dotted_name in parser._required.items():
            self.addRequiredTool(tool_id, dotted_name)

    security.declarePrivate('clear')
    def clear(self):
        self._forbidden = []
        self._required = {}

    #
    #   Helper methods.
    #
    security.declarePrivate('_toolsetConfig')
    _toolsetConfig = PageTemplateFile('tscExport.xml', _xmldir,
                                      __name__='toolsetConfig')

InitializeClass(ToolsetRegistry)


class ProfileRegistry(Implicit):

    """ Track registered profiles.
    """
    implements(IProfileRegistry)

    security = ClassSecurityInfo()
    security.setDefaultAccess('allow')

    def __init__(self):
        self._registered = GlobalRegistryStorage(IProfile)
        self.clear()

    security.declareProtected(ManagePortal, 'getProfileInfo')
    def getProfileInfo(self, profile_id, for_=None):
        """ See IProfileRegistry.
        """
        if profile_id is None:
            # tarball import
            info = {'id': u'',
                    'title': u'',
                    'description': u'',
                    'path': u'',
                    'product': u'',
                    'type': None,
                    'for': None}
            return info
        prefixes = ('profile-', 'snapshot-')
        for prefix in prefixes:
            if profile_id.startswith(prefix):
                profile_id = profile_id[len(prefix):]
                break

        result = self._registered.get(profile_id)
        if result is None:
            raise KeyError(profile_id)
        if for_ is not None:
            if not issubclass(for_, result['for']):
                raise KeyError(profile_id)
        return result.copy()

    security.declareProtected(ManagePortal, 'listProfiles')
    def listProfiles(self, for_=None):
        """ See IProfileRegistry.
        """
        result = []
        for profile_id in self._registered.keys():
            info = self.getProfileInfo(profile_id)
            if for_ is None or issubclass(for_, info['for']):
                result.append(profile_id)
        return tuple(result)

    security.declareProtected(ManagePortal, 'listProfileInfo')
    def listProfileInfo(self, for_=None):
        """ See IProfileRegistry.
        """
        candidates = [ self.getProfileInfo(id)
                       for id in self.listProfiles() ]
        return [ x for x in candidates if for_ is None or x['for'] is None or
                 issubclass(for_, x['for']) ]

    security.declareProtected(ManagePortal, 'registerProfile')
    def registerProfile(self, name, title, description, path, product=None,
                        profile_type=BASE, for_=None):
        """ See IProfileRegistry.
        """
        profile_id = self._computeProfileId(name, product)
        if self._registered.get(profile_id) is not None:
            raise KeyError('Duplicate profile ID: %s' % profile_id)

        info = {'id': profile_id,
                'title': title,
                'description': description,
                'path': path,
                'product': product,
                'type': profile_type,
                'for': for_}

        metadata = ProfileMetadata(path, product=product)()

        # metadata.xml description trumps ZCML description... awkward
        info.update(metadata)

        self._registered[profile_id] = info

    def _computeProfileId(self, name, product):
        profile_id = '%s:%s' % (product or 'other', name)
        return profile_id

    security.declareProtected(ManagePortal, 'unregisterProfile')
    def unregisterProfile(self, name, product=None):
        profile_id = self._computeProfileId(name, product)
        del self._registered[profile_id]

    security.declarePrivate('clear')
    def clear(self):
        self._registered.clear()


InitializeClass(ProfileRegistry)

_profile_registry = ProfileRegistry()
