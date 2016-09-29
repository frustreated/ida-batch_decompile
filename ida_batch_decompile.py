#! /usr/bin/env python
# -*- coding: UTF-8 -*-
# Author : <github.com/tintinweb>
"""
IdaBatchDecompile Plugin and Script adds annotation and batch decompilation functionality to IDA Pro

* requires hexrays decompiler plugin

Usage:

* as idascript in ida gui mode: IDA Pro -> File/Script file... -> IdaDecompileBatch ...
* as idascript in ida cmdline mode: ida(w|w64) -B -M -S"<path_to_this_script> \"--option1\" \"--option2\"", "<target>"
 * see --help for options
* as Plugin: follow ida documentation on how to add python plugins

"""
import sys
import idaapi
import idautils
from idc import *
import json
import glob
from optparse import OptionParser
import logging

logger = logging.getLogger(__name__)


class IdaLocation(object):
    """ Wrap idautils Function
    """

    def __init__(self, location):
        self.at = location
        # self.name = GetFunctionName(location)
        self.name = GetFuncOffset(location)
        self.start = 0
        self.end = 0
        self.func_offset = 0
        try:
            _func = idaapi.get_func(location)
            self.start = _func.startEA
            self.end = _func.endEA  # ==FindFuncEnd(location)
            self.func_offset = self.start - self.at
        except Exception, e:
            logger.exception(e)
        if not self.name:
            self.indirect = True
        else:
            self.indirect = False

    def __repr__(self, *args, **kwargs):
        return "<Function %r at 0x%x (0x%x::0x%x)>" % (self.name, self.at,
                                                       self.start, self.end)

    def get_xrefs(self):
        return (IdaLocation(x.frm) for x in idautils.XrefsTo(self.at))

    def get_coderefs(self):
        return (IdaLocation(frm) for frm in idautils.CodeRefsTo(self.at, 0))

    def as_dict(self):
        return {'at': self.at, 'name': self.name}

    def decompile(self):
        """ decompile function
        """
        try:
            return idaapi.decompile(self.at)
        except idaapi.DecompilationFailure, e:
            return repr(str(e))
        text = str(idaapi.decompile(self.at)).strip()
        '''
        sprintf:
        Python>for w in idaapi.decompile(0x00001578 ).lvars: print w.name
            s
            format
            result
        '''
        # decompile.arguments
        # for w in idaapi.decompile(0x00001EF0 ).lvars: print w.name
        if not grep:
            return text.split('\n')
        # return all lines 
        return [line.strip() for line in text.split('\n') if grep in line]

    def get_function_args(self):
        # find the stack frame
        stack = GetFrame(self.start)
        stack_size = GetStrucSize(stack)
        # figure out all of the variable names
        # base is either ' s' ... saved register or ' r' ... return address
        base = GetMemberOffset(stack, ' s')
        if base == -1:
            base = GetMemberOffset(stack, ' r')
        if base == -1:
            # no ' s' no ' r' assume zero
            base == 0
        stack_vars = []

        for memberoffset in xrange(stack_size):
            previous = stack_vars[-1] if len(stack_vars) else None
            var_name = GetMemberName(stack, memberoffset)
            if not var_name or (previous and var_name == previous.get("name")):
                # skip that entry, already processed
                continue

            offset = GetMemberOffset(stack, var_name) - base
            size = GetMemberSize(stack, memberoffset)
            if previous:
                diff = offset - previous['offset']
                previous['diff_size'] = diff
            stack_vars.append({'name': var_name,
                               'offset': offset,
                               'offset_text': '[bp%Xh]' % offset if offset < 0 else '[bp+%Xh]' % offset,
                               'size': size,
                               'diff_size': size})
        return stack_size, stack_vars


class IdaHelper(object):
    """ Namespace for ida helper functions
    """

    @staticmethod
    def get_functions():
        return (IdaLocation(f) for f in idautils.Functions())

    @staticmethod
    def get_imports():
        for i in xrange(0, idaapi.get_import_module_qty()):
            name = idaapi.get_import_module_name(i)
            if name:
                yield name

    @staticmethod
    def decompile_full(outfile):
        return idaapi.decompile_many(outfile, None, 0)

    @staticmethod
    def annotate_xrefs():
        stats = {'annotated_functions': 0, 'errors': 0}
        for f in IdaHelper.get_functions():
            try:
                function_comment = GetFunctionCmt(f.start, 0)
                if '**** XREFS ****' in function_comment:
                    logger.debug("[i] skipping function %r, already annotated." % f.name)
                    continue
                xrefs = [x.name for x in f.get_coderefs()]
                comment = []
                if function_comment:
                    comment.append(function_comment)
                comment.append("***** XREFS *****")
                comment.append("* # %d" % len(xrefs))
                comment.append(', '.join(xrefs))
                comment.append("*******************")
                SetFunctionCmt(f.start, '\n'.join(comment), 0)
                stats['annotated_functions'] += 1
            except Exception, e:
                print repr(e)
                stats['errors'] += 1
        print "[+] stats: %r" % stats
        print "[+] Done!"

    @staticmethod
    def annotate_functions_with_local_var_size():
        stats = {'annotated_functions': 0, 'errors': 0}
        for f in IdaHelper.get_functions():
            try:
                function_comment = GetFunctionCmt(f.start, 0)
                if '**** Variables ****' in function_comment:
                    logger.debug("[i] skipping function %r, already annotated." % f.name)
                    continue
                size, stack_vars = f.get_function_args()
                comment = []
                if function_comment:
                    comment.append(function_comment)
                comment.append("**** Variables ****")
                comment.append("* stack size: %s" % size)
                for s in stack_vars:
                    comment.append(json.dumps(s))
                comment.append("*******************")
                SetFunctionCmt(f.start, '\n'.join(comment), 0)
                stats['annotated_functions'] += 1
            except Exception, e:
                print repr(e)
                stats['errors'] += 1
        print "[+] stats: %r" % stats
        print "[+] Done!"


class IdaDecompileBatchController(object):
    def __init__(self):
        self.is_windows = sys.platform.startswith('win')
        self.is_ida64 = GetIdbPath().endswith(".i64")  # hackhackhack - check if we're ida64 or ida32
        logger.debug("[+] is_windows: %r" % self.is_windows)
        logger.debug("[+] is_ida64: %r" % self.is_ida64)
        self.my_path = os.path.abspath(__file__)
        self.target_path = idc.GetInputFilePath()
        self.target_file = idc.GetInputFile()
        self.target_dir = os.path.split(self.target_path)[0]
        # settings (form)
        # todo: load from configfile if available.
        self.output_path = None
        self.chk_annotate_stackvar_size = False
        self.chk_annotate_xrefs = False
        self.chk_decompile_imports = False
        self.chk_decompile_imports_recursive = False
        self.chk_decompile_alternative = False
        # self.ida_home = idaapi.idadir(".")
        self.ida_home = GetIdaDirectory()
        # wait for ida analysis to finish
        self.wait_for_analysis_to_finish()
        self.load_plugin_decompiler()

    def wait_for_analysis_to_finish(self):
        logger.debug("[+] waiting for analysis to finish...")
        idaapi.autoWait()
        idc.Wait()
        logger.debug("[+] analysis finished.")

    def load_plugin_decompiler(self):
        # load decompiler plugins (32 and 64 bits, just let it fail)
        logger.debug("[+] trying to load decompiler plugins")
        if self.is_ida64:
            # 64bit plugins
            idc.RunPlugin("hexx64", 0)
        else:
            # 32bit plugins
            idc.RunPlugin("hexrays", 0)
            idc.RunPlugin("hexarm", 0)
        logger.debug("[+] decompiler plugins loaded.")

    def run(self):
        if self.chk_annotate_stackvar_size:
            self.annotate_stack_variable_size()
        if self.chk_annotate_xrefs:
            self.annotate_xrefs()

        if self.chk_decompile_imports:
            if self.chk_decompile_imports_recursive:
                pass
            for image_path in self.enumerate_import_images():
                self.exec_ida_batch_decompile(target = image_path, output = self.output_path,
                                              annotate_stackvar_size = self.chk_annotate_stackvar_size,
                                              annotate_xrefs = self.chk_annotate_xrefs,
                                              imports = self.chk_decompile_imports,
                                              recursive = self.chk_decompile_imports_recursive,
                                              experimental_decomile_cgraph = self.chk_decompile_alternative)

        if self.chk_decompile_alternative:
            raise NotImplemented("Not yet implemented")
            pass
        else:
            pass
            self.decompile_all(self.output_path)

    def annotate_stack_variable_size(self):
        logger.debug("[+] annotating function stack variables")
        IdaHelper.annotate_functions_with_local_var_size()
        logger.debug("[+] done.")

    def annotate_xrefs(self):
        logger.debug("[+] annotating function xrefs")
        IdaHelper.annotate_xrefs()
        logger.debug("[+] done.")

    def enumerate_import_images(self):
        for import_name in IdaHelper.get_imports():
            logger.debug("[i] trying to find image for %r" % import_name)
            for image_path in glob.glob(os.path.join(self.target_dir, import_name) + '*'):
                logger.debug("[i] got image %r" % image_path)
                yield image_path

    def decompile_all(self, outfile=None):
        outfile = outfile or self._get_suggested_output_filename(self.target_path)
        logger.debug("[+] trying to decompile %r as %r" % (self.target_file,
                                                           os.path.split(outfile)[1]))
        IdaHelper.decompile_full(outfile)
        logger.debug("[+] finished decompiling %r as %r" % (self.target_file,
                                                        os.path.split(outfile)[1]))

    def _get_suggested_output_filename(self, target):
        # /a/b/c/d/e/bin.ext
        root, fname = os.path.split(target)
        if fname:
            fname, fext = os.path.splitext(fname)  # bin,ext
        else:
            fname, fext = os.path.splitext(self.target_file)

        # obsolete
        # suggested_outpath = '%s.c'%os.path.join(root,fname)
        # if not os.path.exists(suggested_outpath):
        #    return suggested_outpath
        return '%s.c' % os.path.join(root, fname)

    def exec_ida_batch_decompile(self, target, output, annotate_stackvar_size, annotate_xrefs, imports, recursive,
                                 experimental_decomile_cgraph):
        logger.debug("[+] batch decompile %r" % target)
        # todo: pass commandlines,
        # todo parse commandline
        script_args = ['--output=%s' % output]
        if annotate_stackvar_size:
            script_args.append("--annotate-stackvar-size")
        if annotate_xrefs:
            script_args.append("--annotate-xrefs")
        if imports:
            script_args.append("--imports")
        if recursive:
            script_args.append("--recursive")
        if experimental_decomile_cgraph:
            script_args.append("--experimental-decompile-cgraph")

        script_args = ['\\"%s\\"' % a for a in script_args]
        command = "%s %s" % (self.my_path, ' '.join(script_args))

        ret = self._exec_ida_batch(target, command)
        if ret != 0:
            raise Exception("command failed: %s" % ret)
        return ret

    def _exec_ida_batch(self, target, command):
        # build exe path
        ida_exe = os.path.join(self.ida_home, 'idaw64' if self.is_ida64 else 'idaw')
        if self.is_windows:
            ida_exe += ".exe"
        cmd = [ida_exe, '-B', '-M', '-S"%s"' % command, '"' + target + '"']
        logger.debug(' '.join(cmd))
        logger.debug('[+] executing: %r' % cmd)
        #return 0
        # TODO: INSECURE!
        return subprocess.call(' '.join(cmd), shell=True)


class DecompileBatchForm(Form):
    """
    Form to prompt for target file, backup file, and the address
    range to save patched bytes.
    """

    def __init__(self, idbctrl):
        self.idbctrl = idbctrl
        Form.__init__(self,
                      r"""Batch Decompile ...
{FormChangeCb}
<##Target    :{target}>
<##OutputPath:{outputPath}>
<##Annotate StackVar Size:{chkAnnotateStackVars}>
<##Annotate Func XRefs   :{chkAnnotateXrefs}>
<##Process Imports       :{chkDecompileImports}>
<##Recursive             :{chkDecompileImportsRecursive}>
<##Cgraph (experimental) :{chkDecompileAlternative}>{cGroup1}>
""", {
                          'target': Form.FileInput(swidth=50, open=True, value=idbctrl.target_path),
                          'outputPath': Form.DirInput(swidth=50, value=idbctrl.output_path),
                          'cGroup1': Form.ChkGroupControl(("chkAnnotateStackVars", "chkAnnotateXrefs",
                                                           "chkDecompileImports",
                                                           "chkDecompileImportsRecursive",
                                                           "chkDecompileAlternative")),
                          'FormChangeCb': Form.FormChangeCb(self.OnFormChange),
                      })

        self.Compile()

    def OnFormChange(self, fid):
        # Set initial state
        INIT = -1
        BTN_OK = -2

        if fid == INIT:
            self.EnableField(self.target, False)
            self.EnableField(self.outputPath, False)
            self.EnableField(self.chkDecompileAlternative, False)

        elif fid == BTN_OK:
            self.idbctrl.target = self.target.value

            if self.outputPath.value == '' or os.path.exists(self.outputPath.value):

                self.idbctrl.output_path = self.outputPath.value
            else:
                logger.warning("[!!] output path not valid! %r" % self.outputPath.value)

            self.idbctrl.chk_annotate_stackvar_size = self.chkAnnotateStackVars.checked
            self.idbctrl.chk_decompile_imports = self.chkDecompileImports.checked
            self.idbctrl.chk_decompile_imports_recursive = self.chkDecompileImportsRecursive.checked
            self.idbctrl.chk_annotate_xrefs = self.chkAnnotateXrefs.checked
            self.idbctrl.chk_decompile_alternative = self.chkDecompileAlternative.checked
            logger.debug("[+] config updated")
            return True

        # Toggle backup checkbox
        elif fid == self.chkAnnotateStackVars.id:
            self.chkAnnotateStackVars.checked = not self.chkAnnotateStackVars.checked
        elif fid == self.chkDecompileImports.id:
            self.chkDecompileImports.checked = not self.chkDecompileImports.checked
        elif fid == self.chkDecompileImportsRecursive.id:
            self.chkDecompileImportsRecursive.checked = not self.chkDecompileImportsRecursive.checked
        elif fid == self.chkDecompileAlternative.id:
            self.chkDecompileAlternative.checked = not self.chkDecompileAlternative.checked
        elif fid == self.chkAnnotateXrefs.id:
            self.chkAnnotateXrefs.checked = not self.chkAnnotateXrefs.checked

        return False


class IdaDecompileBatchPlugin(idaapi.plugin_t):
    """ IDA Plugin Base"""
    flags = idaapi.PLUGIN_FIX
    comment = "Batch Decompile"
    help = "github.com/tintinweb"
    wanted_name = "IdaDecompileBatch"
    wanted_hotkey = ""
    wanted_menu = "File/Produce file/", "{} ...".format(IdaDecompileBatchPlugin.wanted_name)

    def init(self):
        NO_HOTKEY = ""
        SETMENU_INS = 0
        NO_ARGS = tuple()

        logger.debug("[+] %s.init()" % self.__class__.__name__)
        self.menuitems = []

        logger.debug("[+] setting up context menus")
        menu = idaapi.add_menu_item(self.wanted_menu[0],
                                    self.wanted_menu[1],
                                    NO_HOTKEY,
                                    SETMENU_INS,
                                    self.menu_config,
                                    NO_ARGS)
        self.menuitems.append(menu)

        return idaapi.PLUGIN_KEEP

    def run(self, arg=None):
        logger.debug("[+] %s.run()" % self.__class__.__name__)

    def term(self):
        logger.debug("[+] %s.term()" % self.__class__.__name__)
        for menu in self.menuitems:
            idaapi.del_menu_item(menu)

    def menu_config(self):
        logger.debug("[+] %s.menu_config()" % self.__class__.__name__)
        if DecompileBatchForm(self.idbctrl).Execute():
            logger.debug("[+] decompiling...")
            self.idbctrl.run()

    def set_ctrl(self, idbctrl):
        logger.debug("[+] %s.set_ctrl(%r)" % (self.__class__.__name__, idbctrl))
        self.idbctrl = idbctrl


def PLUGIN_ENTRY(mode=None):
    """ check execution mode:

        a) as Plugin, return plugin object
        b) as script as part of a batch execution, do not spawn plugin object
     """
    logging.basicConfig(level=logging.DEBUG,
                        format="[%(name)s/%(process)s][%(levelname)-10s] [%(module)s.%(funcName)-14s] %(message)s")
    logger.setLevel(logging.DEBUG)
    # always wait for analysis to finish
    logger.debug("[+] initializing IdaDecompileBatchPlugin")
    # create our controller interface
    idbctrl = IdaDecompileBatchController()
    # parse cmdline
    if mode == '__main__':
        # cmdline mode
        if len(idc.ARGV) > 1:
            # cmdline batch mode
            logger.debug("[+] Mode: commandline")
            parser = OptionParser()
            parser.add_option("-o", "--output", dest="output",
                              help="output path")
            parser.add_option("-S", "--annotate-stackvar-size",
                              action="store_true", default=False,
                              help="Generate stack variable size annotations")
            parser.add_option("-X", "--annotate-xrefs",
                              action="store_true", default=False,
                              help="Generate xref annotations")
            parser.add_option("-I", "--imports",
                              action="store_true", default=False,
                              help="try to decompile files referenced in IAT")
            parser.add_option("-R", "--recursive",
                              action="store_true", default=False,
                              help="Recursive decompile files/imports")
            parser.add_option("-Z", "--experimental-decompile-cgraph",
                              action="store_true", default=False,
                              help="[experimental] decompile funcs referenced in calltree manually")

            options, args = parser.parse_args(idc.ARGV[1:])
            # set options
            idbctrl.output_path = options.output
            idbctrl.chk_annotate_stackvar_size = options.annotate_stackvar_size
            idbctrl.chk_annotate_xrefs = options.annotate_xrefs
            idbctrl.chk_decompile_imports = options.imports
            idbctrl.chk_decompile_imports_recursive = options.recursive
            idbctrl.chk_decompile_alternative = options.experimental_decompile_cgraph
            # set all the idbctrl checkboxes and files
            idbctrl.run()
            idc.Exit(0)
            # return

        logger.debug("[+] Mode: commandline w/o args")
        # PluginMode
        plugin = IdaDecompileBatchPlugin()
        plugin.set_ctrl(idbctrl=idbctrl)
        plugin.init()
        logger.info("[i] %s loaded, see Menu: %s" % (IdaDecompileBatchPlugin.wanted_name,
                                                     IdaDecompileBatchPlugin.wanted_menu))
        #plugin.menu_config()
        return plugin

    else:

        logger.debug("[+] Mode: plugin")
        # PluginMode
        plugin = IdaDecompileBatchPlugin()
        plugin.set_ctrl(idbctrl=idbctrl)
        return plugin

if __name__ == '__main__':
    PLUGIN_ENTRY(mode=__name__)
