from docutils.core import publish_string
from xml.etree.ElementTree import XML, Element, SubElement, ElementTree
import os
import hashlib
from subprocess import Popen, PIPE
import codecs
import sys
import json
from getpass import getpass


def _read(ifile):
    try:
        return ifile.read()
    finally:
        ifile.close()

class Document(object):
    def __init__(self, basepath=None, gitpubPath=None, rest=None, binaryData=None,
                 docmap=None, title=None):
        if gitpubPath:
            self.set_path(basepath, gitpubPath)
            initDict = dict(rst=self.open_rest, jpg=self.open_image,
                            jpeg=self.open_image, png=self.open_image)
            initDict[gitpubPath.split('.')[-1]]() # call the appropriate initializer
        elif rest:
            self.rest = rest
        elif binaryData:
            self.binaryData = binaryData
        else:
            raise ValueError('Document called with no args!')
        self.docmap = docmap
        if title:
            self.title = title

    def set_path(self, basepath, gitpubPath):
        self.path = os.path.join(basepath, gitpubPath)
        self.basepath = basepath
        self.gitpubPath = gitpubPath

    def open_rest(self):
        self.rest = _read(codecs.open(self.path, 'r', 'utf-8'))
        xhtml = publish_string(self.rest, writer_name='xml',
                               settings_overrides=dict(report_level=5))
        x = XML(xhtml) # parse the XML text
        t = x.find('title')
        try:
            self.title = t.text #extract its title
        except AttributeError:
            self.title = 'Untitled'

    def set_content_type(self, contentType=None, filename=None):
        'guess from filename if not provided by caller'
        if contentType:
            self.contentType = contentType
            return
        typeDict = dict(jpg='image/jpeg', jpeg='image/jpeg', png='image/png')
        if filename is None:
            filename = self.path
        self.contentType = typeDict[filename.split('.')[-1]]

    def relative_path(self, relpath):
        'get doc info dict for path relative to this doc, or KeyError'
        gitpubPath = os.path.normpath(os.path.join(os.path.dirname(self.gitpubPath),
                                                   relpath))
        return self.docmap[gitpubPath]

    def open_image(self):
        self.binaryData = _read(file(self.path))
        self.set_content_type()

    def get_hash(self):
        try:
            return unicode_safe_hash(self.rest)
        except AttributeError:
            return hashlib.sha1(self.binaryData).hexdigest()

    def write_rest(self):
        ifile = codecs.open(self.path, 'w', 'utf-8')
        try:
            ifile.write(self.rest)
        finally:
            ifile.close()

    def write(self):
        if hasattr(self, 'rest'):
            self.write_rest()
        else:
            ifile = file(self.path, 'wb')
            try:
                ifile.write(self.binaryData)
            finally:
                ifile.close()


def import_plugin(remoteType):
    'get Repo class from plugin/<remoteType>.py'
    try:
        mod = __import__('plugin.' + remoteType, globals(), locals(), ['Repo'])
    except ImportError:
        raise ImportError('plugin %s not found, or missing Repo class!' % remoteType)
    return mod.Repo


def unicode_safe_hash(s):
    'converts to utf8 before hashing, to avoid hashlib crash on unicode characters'
    e = codecs.getencoder('utf8')
    s2, n = e(s)
    return hashlib.sha1(s2).hexdigest()

def copy_kwargs(kwargs):
    'ensure all keys are str not unicode!'
    d = {}
    for k,v in kwargs.items():
        d[str(k)] = v
    return d

def save_json(path, d):
    'save Python data to JSON file'
    ifile = open(path, 'w')
    try:
        json.dump(d, ifile, sort_keys=True, indent=4)
        print >>ifile # make sure file ends in newline
    finally:
        ifile.close()

class DocMap(object):
    def __init__(self):
        self.revDict = {} # map from remote docID to attribute dictionary
        self.dict = {} # map from gitpubPath to attribute dictionary

    def init_from_file(self, path):
        'initialize mapping from saved json file'
        ifile = open(path)
        try:
            d = json.load(ifile)
            remoteType = d['remoteType']
            repoArgs = d['repoArgs']
            self.dict = d['docDict']
            self.revDict = d['revDict']
        finally:
            ifile.close()
        return remoteType, copy_kwargs(repoArgs)

    def save_file(self, path, remoteType, repoArgs):
        'save dict and revDict to our json file'
        d = dict(remoteType=remoteType, repoArgs=repoArgs, docDict=self.dict,
                 revDict=self.revDict)
        save_json(path, d)

    def copy(self):
        'return a copy of this docmap'
        m = self.__class__()
        m.revDict.update(self.revDict)
        m.dict.update(self.dict)
        return m

    def mv(self, oldpath, newpath):
        'alter mapping to replace oldpath with newpath'
        d = self.dict[oldpath]
        d['gitpubPath'] = newpath
        del self.dict[oldpath]
        self.dict[newpath] = d
        try:
            gitpubID = d['gitpubID']
            self.revDict[gitpubID] = d
        except KeyError:
            pass

    def __contains__(self, gitpubPath):
        return gitpubPath in self.dict

    def __getitem__(self, gitpubPath):
        'raises KeyError if not found'
        return self.dict[gitpubPath]

    def __setitem__(self, gitpubPath, docDict):
        'add mapping for a local document, to a dict of doc-attributes'
        self._del_rev_mapping(gitpubPath)
        docDict['gitpubPath'] = gitpubPath
        self.dict[gitpubPath] = docDict
        try:
            self.revDict[docDict['gitpubID']] = docDict
        except KeyError: # document not yet published in remote, ok
            pass

    def _del_rev_mapping(self, gitpubPath):
        'delete current gitpubID reverse mapping for this gitpubPath'
        try:
            del self.revDict[self.dict[gitpubPath]['gitpubID']]
        except KeyError: # document not yet published in remote, ok
            pass

    def __delitem__(self, gitpubPath):
        'delete mapping for a local document, to delete it from remote repo'
        self._del_rev_mapping(gitpubPath)
        del self.dict[gitpubPath]

    def delete_remote_mapping(self, gitpubID):
        'delete mapping associated with a remote doc ID'
        try:
            del self.dict[self.revDict[gitpubID]['gitpubPath']]
        except KeyError:
            pass
        del self.revDict[gitpubID]

    def __sub__(self, oldmap):
        'get analysis of doc differences vs. oldmap'
        return DocMapDiff(self, oldmap)

    def update(self, basepath):
        'update all gitpubHash values based on current file contents'
        docChanged = False
        for gitpubPath,d in self.dict.items():
            doc = Document(basepath, gitpubPath)
            gitpubHash = doc.get_hash()
            if gitpubHash != d.get('gitpubHash', ''):
                d['gitpubHash'] = gitpubHash
                docChanged = True
        return docChanged # report whether any doc got updated

class DocMapDiff(object):
    '''Records the diff between two DocMap objects.
    Sets 3 attributes:
    newDocs: gitpubPath present in newmap.dict but neither it nor its
             associated gitpubID present in oldmap.
    changedDocs: gitpubHash changed (or missing) in oldmap.dict
    deletedDocs: gitpubID present in oldmap.revDict but not newmap.revDict'''
    def __init__(self, newmap, oldmap):
        self.newmap = newmap
        self.oldmap = oldmap
        newDocs = []
        deletedDocs = []
        changedDocs = []
        for k,docinfo in newmap.dict.items():
            gitpubID = docinfo.get('gitpubID', None)
            if not gitpubID: # never published before
                newDocs.append(k)
            elif k in oldmap.dict:
                if gitpubID != oldmap.dict[k]['gitpubID']:
                    raise ValueError('gitpubID mismatch:%s != %s'
                                     % (gitpubID, oldmap.dict[k]['gitpubID']))
                try:
                    if docinfo['gitpubHash'] != oldmap.dict[k]['gitpubHash']:
                        raise KeyError
                except KeyError:
                    changedDocs.append(k)
            elif gitpubID in oldmap.revDict:
                try:
                    if docinfo['gitpubHash'] \
                           != oldmap.revDict[gitpubID]['gitpubHash']:
                        raise KeyError
                except KeyError:
                    changedDocs.append(k)
            else:
                raise ValueError('gitpubID missing from lastpush.json: ' +
                                 gitpubID)
        for gitpubID in oldmap.revDict:
            if gitpubID not in newmap.revDict:
                deletedDocs.append(gitpubID)
        self.newDocs = newDocs
        self.deletedDocs = deletedDocs
        self.changedDocs = changedDocs


class Remote(object):
    def __init__(self, name, basepath, remoteType=None, repoArgs=None,
                 importDir='%s-import'):
        self.name = name
        self.basepath = basepath
        self.importDir = importDir
        self.path = os.path.join(basepath, '.gitpub', name + '.json')
        if not os.path.isdir(os.path.join(basepath, '.gitpub')): # create dir if needed
            os.mkdir(os.path.join(basepath, '.gitpub'))
        self.docmap = DocMap()
        try:
            remoteType, repoArgs = self.docmap.init_from_file(self.path)
            newRemote = False
        except IOError:
            newRemote = True
        klass = import_plugin(remoteType)
        self.repo = klass(**repoArgs)
        self.remoteType = remoteType
        self.repoArgs = repoArgs
        ## if newRemote:
        ##     try:
        ##         self.fetch()
        ##     except ValueError:
        ##         docDict = self.repo.list_documents()
        ##         self.docmap.init_from_repo(self.path, remoteType, repoArgs,
        ##                                    docDict)

    def save_doc_map(self, lastPush=False):
        if lastPush:
            path = os.path.join(self.basepath, '.gitpub',
                                self.name + '.lastpush.json')
        else:
            path = self.path
        self.docmap.save_file(path, self.remoteType, self.repoArgs)
        return path
                
    def push(self, newmap=None):
        if newmap is None: # send changes since last push, based on saved docmap
            oldmap = DocMap()
            oldmap.init_from_file(os.path.join(self.basepath, '.gitpub',
                                               self.name + '.lastpush.json'))
            newmap = self.docmap
            diff = self.docmap - oldmap
        else:
            diff = newmap - self.docmap # analyze doc map changes
        unresolvedRefs = set()
        for gitpubPath in diff.newDocs: # publish new docs on remote repo
            newdoc = Document(self.basepath, gitpubPath, docmap=self.docmap)
            docDict = copy_kwargs(newmap.dict[gitpubPath])
            docDict['gitpubHash'] = newdoc.get_hash()
            docDict.update(self.repo.new_document(newdoc,
                                  unresolvedRefs=unresolvedRefs, **docDict))
            self.docmap[gitpubPath] = docDict
        for gitpubPath in diff.changedDocs: # update changed docs on remote repo
            newdoc = Document(self.basepath, gitpubPath, docmap=self.docmap)
            docDict = copy_kwargs(newmap.dict[gitpubPath])
            docDict['gitpubHash'] = newdoc.get_hash()
            d = self.repo.set_document(docDict['gitpubID'], newdoc,
                                  unresolvedRefs=unresolvedRefs, **docDict)
            if d: # allow set_document() to update our document attrs
                docDict.update(d)
            self.docmap[gitpubPath] = docDict
            
        for gitpubID in diff.deletedDocs: # remove deleted docs from remote repo
            self.repo.delete_document(gitpubID)
            self.docmap.delete_remote_mapping(gitpubID)
        self.resolve_refs(self.docmap, unresolvedRefs)

    def resolve_refs(self, docmap, unresolvedRefs):
        'resend docs with unresolved refs, until they resolve'
        while unresolvedRefs:
            newUR = set()
            for doc in unresolvedRefs:
                docDict = docmap[doc.gitpubPath]
                self.repo.set_document(docDict['gitpubID'], doc,
                                       unresolvedRefs=newUR,
                                       **clean_kwargs(docDict))
            if len(newUR) >= len(unresolvedRefs):
                print 'unable to resolve refs!', [doc.title for doc in newUR]
                return
            unresolvedRefs = newUR

    def fetch_setup(self):
        importDir = os.path.join(self.basepath, self.importDir % self.name)
        if not os.path.isdir(importDir): # create dir if needed
            os.mkdir(importDir)
        try:
            self.repo.get_document
        except AttributeError:
            raise ValueError('this remote does not support fetch!')
        docDict = self.repo.list_documents()
        return importDir, docDict

    def fetch_latest(self):
        'retrieve docs from remote, save changed docs and return them as list'
        importDir, docDict = self.fetch_setup()
        l = []
        for gitpubID in docDict:
            gitpubPath = self.import_doc(gitpubID, importDir)
            if gitpubPath:
                l.append(gitpubPath)
        return l

    def import_doc(self, gitpubID, importDir, **kwargs):
        'retrieve the specified doc from the remote repo, save to importDir'
        try: # use existing file mapping if present
            gitpubPath = self.docmap.revDict[gitpubID]['gitpubPath']
            path = os.path.join(self.basepath, gitpubPath)
        except KeyError: # use default import path
            path = os.path.join(importDir, gitpubID + '.rst')
            gitpubPath = relpath(path, self.basepath)
        try:
            doc, d = self.repo.get_document(gitpubID, **kwargs)
        except StandardError:
            print >>sys.stderr, 'failed to get document %s.  Conversion error? Skipping' % gitpubID
            return None
        docDict = dict(gitpubPath=gitpubPath, gitpubID=gitpubID)
        for k,v in d.items(): # copy relevant attributes from returned dict
            if k.startswith('gitpub'):
                docDict[k] = v
        if 'gitpubHash' not in docDict:
            docDict['gitpubHash'] = doc.get_hash()
        try:
            if docDict['gitpubHash'] == self.docmap.revDict[gitpubID]['gitpubHash']:
                return None # matches existing content, no need to update
        except KeyError:
            pass
        doc.set_path(self.basepath, gitpubPath)
        doc.write()
        self.docmap[gitpubPath] = docDict
        return gitpubPath

def clean_kwargs(kwargs):
    'return copy of kwargs w/o gitpub* keys'
    d = {}
    for k,v in kwargs.items():
        if not k.startswith('gitpub'):
            d[str(k)] = v
    return d
    


class TrackingBranch(object):
    def __init__(self, name, localRepo=None, branchName='master', doFetch=False,
                 autoCreate=False, doCheckout=False, **kwargs):
        '''create the branch if not present'''
        self.branchName = '/'.join(('gpremotes', name, branchName))
        if localRepo is None:
            localRepo = GitRepo() # search upwards for top of git repository
        self.localRepo = localRepo
        doCommit = False
        if self.branchName not in localRepo.branches:
            if autoCreate:
                localRepo.branch(self.branchName) # create new branch
                localRepo.checkout(self.branchName) # ready to work on this branch
                doCommit = True
            else:
                raise ValueError('no such gitpublish remote branch in this repo!')
        elif doCheckout:
            localRepo.checkout(self.branchName)
        self.remote = Remote(name, localRepo.basepath, **kwargs)
        if doFetch and self.fetch():
            doCommit = False # fetch already performed commit!
        if doCommit: # need to commit auto-created mapping files
            self.commit('create new tracking branch', False, lastPush=True)

    def merge(self, branchName='master', updateOnly=False):
        'run git merge and then scan for docmap changes, and commit them'
        self.localRepo.checkout(self.branchName)
        if not updateOnly: # skip if user has already run git merge manually
            self.localRepo.merge(branchName)
        mapChanged = self.merge_moves()
        docmap = self.get_stage()
        mapChanged |= docmap.update(self.localRepo.basepath) # what changed?
        if mapChanged: # need to commit updated doc map
            self.commit('updated %s docmap from %s'
                        % (self.branchName, branchName)) # commit new docmap
        else: # nothing staged, so clear the staging buffer
            del self.stage

    def push(self, branchName='master', updateOnly=False, newmap=None):
        'push changes to remote and commit map changes'
        self.merge(branchName, updateOnly) # merge changes from branch
        self.remote.push(newmap) # actually send the changes to the remote
        self.commit(message='publish doc changes to remote %s'
                    % self.remote.name, fromStage=False, lastPush=True)

    def get_stage(self):
        'return temporary docmap where we can add changes before committing them'
        try:
            docmap = self.stage
        except AttributeError:
            docmap = self.stage = self.remote.docmap.copy()
        return docmap
        
    def add(self, path, **docDict):
        'add a file to be staged for next commit'
        gitpubPath = relpath(path, self.localRepo.basepath)
        self.localRepo.add(path)
        docmap = self.get_stage()
        docmap[gitpubPath] = docDict

    def rm(self, path):
        'stage a file to be deleted in next commit'
        gitpubPath = relpath(path, self.localRepo.basepath)
        docmap = self.get_stage()
        del docmap[gitpubPath]

    def update_map_move(self, oldpath, newpath):
        'update map to reflect changed path of a file'
        oldpath = relpath(oldpath, self.localRepo.basepath)
        newpath = relpath(newpath, self.localRepo.basepath)
        if oldpath not in self.remote.docmap: # not published in our doc map
            return False # so no need to modify the doc map
        docmap = self.get_stage()
        docmap.mv(oldpath, newpath)
        return True # we altered the doc map

    def merge_moves(self):
        '''find file-moves present in _git_moves.json but not
        in _git_moves_merged.json.  Then apply them to our docmap,
        and add them to _git_moves_merged.json, commiting its changes
        if any.'''
        moves, path = self.localRepo.get_move_dict()
        movesMerged, path = self.localRepo.\
                            get_move_dict('_git_moves_merged.json')
        changed = mapChanged = False
        for oldpath, d in moves.items():
            if len(d) > 1:
                raise ValueError('same file moved more than once?!?')
            commitID, newpath = d.items()[0]
            try:
                commits = movesMerged[oldpath]
                if commits[commitID] != newpath:
                    raise ValueError('move-merge mismatch: %s[%s]=%s != %s'
                                     % (oldpath, commitID, newpath,
                                        commits[commitID]))
            except KeyError: # add this to movesMerged
                mapChanged |= self.update_map_move(oldpath, newpath)
                movesMerged.setdefault(oldpath, {})[commitID] = newpath
                changed = True
        if changed:
            save_json(path, movesMerged)
            self.localRepo.add(path)
            self.localRepo.commit(message='updated _git_moves_merged.json')
        return mapChanged

    def mv(self, oldpath, newpath):
        'just a wrapper: localRepo will save move info for later merging'
        self.localRepo.mv(oldpath, newpath)

    def save_stage(self):
        'save staged docmap to file and tell DVCS to stage it for next commit'
        self.remote.docmap = self.stage
        self.remote.save_doc_map()
        self.localRepo.add(self.remote.path)        

    def commit(self, message, fromStage=True, repoState=None, lastPush=False):
        'commit map changes to our associated tracking branch in the local repo'
        if fromStage and not hasattr(self, 'stage'):
            raise AttributeError('no changes to commit')
        if repoState is None:
            repoState = self.localRepo.push_state()
            self.localRepo.checkout(self.branchName)
        if fromStage:
            self.remote.docmap = self.stage
        self.localRepo.add(self.remote.save_doc_map())
        if lastPush: # save copy of mapping as last synch with remote
            self.localRepo.add(self.remote.save_doc_map(lastPush=True))
        self.localRepo.commit(message=message)
        repoState.pop()
        if fromStage:
            del self.stage # moved this docmap to self.remote...

    def fetch_latest(self):
        'fetch latest state from remote, and commit any changes in this branch'
        newdocs = self.remote.fetch_latest()
        if len(newdocs) == 0:
            return False
        for gitpubPath in newdocs:
            self.localRepo.add(os.path.join(self.localRepo.basepath, gitpubPath))
        return True

    def fetch_doc_history(self, history_f):
        'commit each doc revision in temporal order'
        importDir, docDict = self.remote.fetch_setup()
        l = []
        for gitpubID in docDict:
            docHistory = history_f(gitpubID)
            for revID, d in docHistory.items():
                l.append((d['timestamp'], gitpubID, revID, d))
        if len(l) == 0:
            return False
        l.sort() # sort in temporal order
        revCommits = {}
        for t, gitpubID, revID, d in l:
            try:
                if revID in self.remote.docmap[gitpubPath]['revCommit']:
                    continue # already retrieved & committed this file rev
            except KeyError:
                pass
            gitpubPath = self.remote.import_doc(gitpubID, importDir, revID=revID)
            self.localRepo.add(os.path.join(self.localRepo.basepath, gitpubPath))
            commitID = self.localRepo.commit('%s revision %s on %s'
                                             % (t.ctime(), str(revID), str(gitpubID)))
            d2 = self.remote.docmap[gitpubPath]
            try: # save the commit ID mapping info
                revCommits[gitpubPath][revID] = commitID
            except KeyError:
                revCommits[gitpubPath] = {revID:commitID}
            d2['revCommit'] = revCommits[gitpubPath]
            self.remote.docmap[gitpubPath] = d2 # save updated metadata
        return True

    def fetch(self):
        'fetch doc history (if repo supports this) or latest snapshot'
        repoState = self.localRepo.push_state()
        self.localRepo.checkout(self.branchName)
        try:
            history_f = self.remote.repo.get_document_history
            msg = 'updated doc mappings and revision history from fetch'
        except AttributeError:
            doCommit = self.fetch_latest()
            msg = 'fetch from remote'
        else:
            doCommit = self.fetch_doc_history(history_f)
        if doCommit:
            self.commit(msg, False, repoState, lastPush=True)
        return doCommit # report whether we performed a commit or not


try:
    relpath = os.path.relpath # python 2.6+
except AttributeError: # for earlier python versions
    def relpath(path, basepath=None):
        if basepath is None:
            basepath = os.getcwd()
        path = os.path.abspath(path)
        if path.startswith(basepath):
            return path[len(basepath) + 1:]
        else:
            raise ValueError('path not inside basepath!')
    

def run_subprocess(args, errmsg):
    'raise OSError if nonzero exit code'
    p = Popen(args)
    p.wait()
    if p.returncode:
        raise OSError(errmsg % p.returncode)


class GitRepoState(object):
    def __init__(self, repo):
        self.repo = repo
        self.branch = repo.branch()

    def pop(self):
        self.repo.checkout(self.branch)
        

class GitRepo(object):
    def __init__(self, basepath=None):
        'basepath should be top of the git repository, i.e. dir containing .git dir'
        if basepath is None:
            basepath = os.getcwd() # search for .git repo containing cwd
            while True:
                if os.path.isdir(os.path.join(basepath, '.git')):
                    break # basepath is top-level of git repo
                basepath, tail = os.path.split(basepath) # move up one dir
                if len(basepath) <= 1: # root directory
                    raise ValueError('not inside a git repository!')
        self.basepath = basepath
        self.branches = self.list_branches()

    def checkout(self, branchname):
        'git checkout <branchname>'
        if branchname == self.list_branches()[0]:
            return # already on this branch, no need to do anything
        run_subprocess(('git', 'checkout', branchname), 'git checkout error %d')
        self.branches = self.list_branches()

    def merge(self, branchName):
        'git merge <branchName>'
        run_subprocess(('git', 'merge', branchName), 'git merge error %d')

    def add(self, path):
        'git add <path>'
        path = relpath(path) # relative to current directory
        run_subprocess(('git', 'add', path), 'git add error %d')

    def rm(self, path):
        'git rm <path>'
        path = relpath(path) # relative to current directory
        run_subprocess(('git', 'rm', path), 'git rm error %d')

    def _mv(self, oldpath, newpath):
        'internal interface to run git mv <oldpath> <newpath>'
        oldpath = relpath(oldpath) # relative to current directory
        newpath = relpath(newpath) # relative to current directory
        run_subprocess(('git', 'mv', oldpath, newpath), 'git mv error %d')

    def commit(self, message):
        'commit and return its commit ID'
        run_subprocess(('git', 'commit', '-m', message), 'git commit error %d')
        l = Popen(["git", "log", 'HEAD^..HEAD'], stdout=PIPE).communicate()[0].split('\n')
        return l[0].split()[1] # return our commit ID

    def branch(self, branchname=None):
        'create new branch, or return current branch'
        if branchname == self.list_branches()[0]:
            return # already on this branch, no need to do anything
        elif branchname: # switch to specified branch
            run_subprocess(('git', 'branch', branchname), 'git branch error %d')
            self.branches.append(branchname)
        else: # get the current branch name
            return self.list_branches()[0]

    def list_branches(self):
        'list existing branches, with current branch first'
        l = Popen(["git", "branch"], stdout=PIPE).communicate()[0].split('\n')[:-1]
        l.sort(reverse=True) # force starred branch to be first
        return [s[2:] for s in l]

    def push_state(self):
        'get a state object representing current git repo state'
        return GitRepoState(self)

    def get_last_commit_id(self):
        'get ID of the most recent commit'
        return Popen(['git', 'log', '--pretty=oneline', '-1'], stdout=PIPE).\
            communicate()[0].split()[0]
        
    def get_move_dict(self, filename='_git_moves.json'):
        'read moves-registry dict stored using JSON'
        path = os.path.join(self.basepath, '.gitpub', filename)
        try:
            ifile = open(path)
        except IOError:
            d = {} # create empty move registry
        else:
            try:
                d = json.load(ifile) # read the existing move-registry
            finally:
                ifile.close()
        return d, path

    def record_move(self, oldpath, newpath):
        'record this file move for later merging to remote tracking branch'
        d, path = self.get_move_dict()
        oldpath = relpath(oldpath, self.basepath)
        newpath = relpath(newpath, self.basepath)
        lastCommit = self.get_last_commit_id()
        d.setdefault(oldpath, {})[lastCommit] = newpath # add this move
        dirpath = os.path.dirname(path)
        if not os.path.isdir(dirpath): # create .gitpub directory if needed
            os.mkdir(dirpath)
        save_json(path, d)
        return path

    def mv(self, oldpath, newpath):
        'perform move while recording info needed for merge to tracking branch'
        if self.branch().startswith('gpremotes/'):
            raise ValueError('''You should not run this command in a remote
tracking branch (%s), but instead in your local
branch (%s).  Then the gitpublish merge command will
automatically merge these changes into your remote
tracking branch.''')
        path = self.record_move(oldpath, newpath) # record in move-registry
        self.add(path) # add move-registry for next commit
        self._mv(oldpath, newpath) # run git mv
    

class RepoBase(object):
    '''Base class for plugin Repo classes, e.g. see plugins/blogger.py '''
    def __init__(self, host, user, password=None, blog_id=0):
        self.host = host
        self.user = user
        self.password = password
        self.blog_id = int(blog_id)

    def check_password(self, attr='password'):
        'ask user for password if not already stored'
        if getattr(self, attr, None) is None:
            setattr(self, attr, getpass('Enter %s for %s on %s:' %
                                        (attr, self.user, self.host)))

    def new_document(self, doc, pubtype='post', publish=True, gitpubHash=None,
                     unresolvedRefs=None, *args, **kwargs):
        'post a restructured text file to wordpress as post or page'
        self.check_password()
        if hasattr(doc, 'rest'):
            html = self.convert_rest(doc, unresolvedRefs)
        else:
            return self.upload_file(doc)
        if gitpubHash: # insert our hash code as HTML comment
            html += '\n<!-- gitpubHash=%s -->\n' % gitpubHash
        d = dict(title=doc.title, description=html)
        if pubtype == 'page':
            gitpubID = 'page:' + str(self.new_page(doc.title, html, publish))
        else:
            gitpubID = 'post:' + str(self.new_post(doc.title, html, publish))
        return dict(gitpubID=gitpubID, gitpubRemotePath='/?p=' + gitpubID[5:])

    def upload_file(self, doc, doc_id=None):
        'upload file to WP server for inclusion in documents'
        if doc_id:
            wpName = doc_id.split('/')[-1]
            if wpName.startswith('wpid-'):
                wpName = wpName[5:]
        else:
            wpName = os.path.basename(doc.gitpubPath)
        content = dict(name=wpName, type=doc.contentType,
                       bits=xmlrpclib.Binary(doc.binaryData), overwrite=True)
        result = self.server.wp.uploadFile(self.blog_id, self.user, self.password,
                                           content)
        urlSplit = result['url'].split('/')
        if urlSplit[2] == self.host: # just save as local path
            gitpubRemotePath = '/' + '/'.join(urlSplit[3:])
        else: # not on the same host, so must save URL as absolute path
            gitpubRemotePath = result['url']
        return dict(gitpubID='file:' + gitpubRemotePath,
                    gitpubRemotePath=gitpubRemotePath,
                    gitpubUnlisted=True) # WP only lists pages & posts, not files

    def _get_pubtype_id(self, doc_id):
        pubtype = doc_id.split(':')[0]
        pub_id = doc_id[len(pubtype) + 1:]
        return pubtype, pub_id

    def get_document(self, doc_id):
        'retrieve the specified post or page and convert to ReST'
        self.check_password()
        pubtype, pub_id = self._get_pubtype_id(doc_id)
        if pubtype == 'page':
            html, result = self.get_page(pub_id)
        elif pubtype == 'post':
            html, result = self.get_post(pub_id)
        else:
            raise ValueError('no method to get pubtype: %s' % pubtype)
        try: # extract our hash code if present
            i = html.index('gitpubHash=')
            result['gitpubHash'] = html[i + 11:i + 100].split()[0]
            j = html[i:].index('>')
            html = html[:i-5] + html[i + j + 1:] # remove inserted comment
        except ValueError:
            pass
        buf = StringIO()
        parser = html2rest.Parser(buf)
        parser.feed(html)
        parser.close()
        rest = buf.getvalue()
        doc = core.Document(rest=rest, title=result.get('title', 'Untitled'))
        result['gitpubRemotePath'] = '/?p=' + pub_id
        return doc, result
            
    def set_document(self, doc_id, doc, publish=True, gitpubHash=None,
                     unresolvedRefs=None, *args, **kwargs):
        'post a restructured text file to wordpress as the specified doc_id'
        self.check_password()
        pubtype, pub_id = self._get_pubtype_id(doc_id)
        if pubtype == 'file':
            return self.upload_file(doc, doc_id)
        html = self.convert_rest(doc, unresolvedRefs)
        if gitpubHash: # insert our hash code as HTML comment
            html += '\n<!-- gitpubHash=%s -->\n' % gitpubHash
        d = dict(title=doc.title, description=html)
        if pubtype == 'page':
            v = self.update_page(pub_id, doc.title, html, publish)
        elif pubtype == 'post':
            v = self.update_post(pub_id, doc.title, html, publish)
        else:
            raise ValueError('unknown pubtype: %s' % pubtype)
        if not v:
            raise ValueError('xmlrpc server method failed: check your args')

    def delete_document(self, doc_id, publish=True, *args, **kwargs):
        'delete a post or page from the WP server'
        self.check_password()
        pubtype, pub_id = self._get_pubtype_id(doc_id)
        if pubtype == 'page':
            v = self.delete_page(pub_id)
        elif pubtype == 'post':
            v = self.delete_post(pub_id)
        elif pubtype == 'file':
            v = self.delete_file(doc_id)
        else:
            raise ValueError('unknown pubtype: %s' % pubtype)
        if not v:
            raise ValueError('xmlrpc server method failed: check your args')

    def list_documents(self, maxposts=2000):
        'get list of posts and pages from server, return as dictionary'
        self.check_password()
        d = {}
        l = self.get_post_list(maxposts)
        for kwargs in l:
            d['post:' + str(kwargs['postid'])] = kwargs
        l = self.get_page_list(maxposts)
        for kwargs in l:
            d['page:' + str(kwargs['page_id'])] = kwargs
        return d



