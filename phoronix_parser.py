#!/usr/bin/env python3

import git
import os
import glob
from xml.dom import minidom
import urllib.request
import hashlib
from shutil import copy2 as cp
import stat
from sys import platform
import subprocess
from select import select
from contextlib import contextmanager
import json
import fileinput
import progressbar
import traceback

REMOTE_BENCH_ROOT_PATH = os.path.join("ob-cache", "test-profiles", "pts")
file_dir = os.path.dirname(os.path.abspath(__file__))
clone_dir = os.path.join(file_dir, "phoronix-benchs")
bench_root_path = os.path.join(clone_dir, REMOTE_BENCH_ROOT_PATH)
install_dir = os.path.join(file_dir, "phoronix-converted")
implementation_template = os.path.join(file_dir, "phoronix_implementation.py.template")
benchmark_info_template = os.path.join(file_dir, "phoronix_benchmark_info.json.template")

installer_map = {"linux": "install.sh",
                 "linux2": "install.sh",
                 "darwin": "install_macosx.sh",
                 "windows": "install_windows.sh"}
bench_dict = {}


class ProgressBar():
    def __init__(self):
        self.pbar = None

    def __call__(self, block_num, block_size, total_size):
        if not self.pbar:
            self.pbar = progressbar.ProgressBar(maxval=total_size if total_size > 0 else 0)
            self.pbar.start()

        downloaded = block_num * block_size
        if downloaded < total_size:
            self.pbar.update(downloaded)
        else:
            self.pbar.finish()


@contextmanager
def pipe():
    r, w = os.pipe()
    yield r, w
    os.close(r)
    os.close(w)


def generate_dict():
    for bench in sorted(os.listdir(bench_root_path)):
        bench_name, bench_v = bench.rsplit('-', 1)

        if bench_name not in bench_dict:
            bench_dict[bench_name] = {}

        for p, installer_name in installer_map.items():
            if os.path.isfile(os.path.join(bench_root_path, bench, installer_name)):
                if 'versions' not in bench_dict[bench_name]:
                    bench_dict[bench_name]['versions'] = {}

                if bench_v in bench_dict[bench_name]['versions']:
                    bench_dict[bench_name]['versions'][bench_v].append(p)
                else:
                    bench_dict[bench_name]['versions'][bench_v] = [p]


def phoronix_init():
    """
    Function responsible of cloning and syncing the local clone of phoronix definitions.
    This function is idempotent.
    """
    if not os.path.isdir(clone_dir):
        os.mkdir(clone_dir)

    repo = git.Repo.init(clone_dir)

    try:
        repo.create_remote("origin", "https://github.com/phoronix-test-suite/phoronix-test-suite")
    except git.exc.GitCommandError:
        print("Origin already set up.")

    repo.config_writer().set_value("core", "sparsecheckout", "true").release()

    sparse_checkout_info_file_path = os.path.join(clone_dir, ".git", "info", "sparse-checkout")

    if os.path.isfile(sparse_checkout_info_file_path):
        sparse_checkout_info_file = open(sparse_checkout_info_file_path, "w")
    else:
        sparse_checkout_info_file = open(sparse_checkout_info_file_path, "x")
    sparse_checkout_info_file.write(REMOTE_BENCH_ROOT_PATH)
    sparse_checkout_info_file.close()

    rebase = False
    try:
        repo.git.reset('--hard', 'origin/master')
        rebase = True
    except Exception:
        print("Nothing to reset")

    repo.remotes.origin.pull("master", rebase=rebase)


def phoronix_list(benchmark_name=None, plat=platform):
    """
    Function capable of listing all the versions of a given benchmark.
    """
    if benchmark_name is None or not benchmark_name:
        if not bench_dict:
            generate_dict()
        for bench_name, bench_data in bench_dict.items():
            for v, p in bench_data['versions'].items():
                if platform in p:
                    print(f"{bench_name} @ {v} [{platform}]")
    else:
        local_benchmark_repo = os.path.join(clone_dir, REMOTE_BENCH_ROOT_PATH, benchmark_name)
        results = glob.glob(os.path.join(bench_root_path, local_benchmark_repo + "*"))
        if results:
            if not bench_dict:
                generate_dict()
            for v, p in bench_dict[benchmark_name]['versions'].items():
                if platform in p:
                    print(f"{benchmark_name} @ {v} [{platform}]")
        else:
            raise Exception("Benchmark {} not found.".format(benchmark_name))
    pass


def phoronix_exists(benchmark_name, benchmark_v=None):
    """
    Function returning a boolean flag relative to existence of a given bench (with optional version).
    """
    if benchmark_name:
        if not bench_dict:
            generate_dict()
        if benchmark_name in bench_dict:
            if benchmark_v:
                return benchmark_v in bench_dict[benchmark_name]['versions']
            else:
                return True
        else:
            raise Exception("Benchmark name not valid.")
    else:
        raise Exception("Please provide a non-empty benchmark name.")


def safe_mkdir(path):
    """
    An mkdir wrapper to avoid calling mkdir on existing directorires.
    """
    if not os.path.isdir(path):
        os.mkdir(path)


def file_inplace_replace(file_path, search_string, replace_string):
    """
    A function working very much as replace, but inplace on files.
    """
    with fileinput.FileInput(file_path, inplace=True) as file:
        for line in file:
            print(line.replace(search_string, replace_string), end='')


def convert_settings(settings_list, settings_dir):
    """
    Function which parses phoronix-defined CLI args converting them into json setting files.
    It iterates over a list of XML elements in the form (extracted from a test-definition.xml):

    <Entry>
        <Name>Fast</Name>
        <Value>-fast</Value>
    </Entry>
    <Entry>
        <Name>Medium</Name>
        <Value>-medium</Value>
    </Entry>
    <Entry>
        <Name>Thorough</Name>
        <Value>-thorough</Value>
    </Entry>
    <Entry>
        <Name>Exhaustive</Name>
        <Value>-exhaustive</Value>
    </Entry>

    """
    default_settings_file = ''
    save_default_settings = True

    safe_mkdir(settings_dir)

    for setting in settings_list:
        name = setting.getElementsByTagName('Name')[0].firstChild.nodeValue.lower()
        cli_args = setting.getElementsByTagName('Value')[0].firstChild.nodeValue
        dict = {"cli_args": cli_args}
        with open(os.path.join(settings_dir, f"settings-{name}.json"), 'w+') as outfile:
            json.dump(dict, outfile)
            if save_default_settings:
                default_settings_file = f"settings-{name}.json"
                save_default_settings = False

    return default_settings_file


def install_installers(bench_path, target_dir):
    """
    A function which copies and chmod+x the installer scripts coming with phoronix benchmarks.
    Typical names are install.sh, install_macosx.sh, install_windows.sh .
    """
    for installer in glob.glob(os.path.join(bench_path, "install*.sh")):
        cp(installer, target_dir)
        installer_path = os.path.join(target_dir, installer)
        os.chmod(installer_path, os.stat(installer_path).st_mode | stat.S_IEXEC)


def create_implementation_file(target_dir, benchmark_name, benchmark_path):
    """
    A function which creates an OpenForBC implementation file from a template.
    The command is set by parsing the installer description.
    The arguments are by default the "cli_args" from the converted settings.
    """
    target_implementation_file = os.path.join(target_dir, "implementation.py")
    cp(implementation_template, target_implementation_file)
    benchmark_command = benchmark_name
    with open(os.path.join(benchmark_path, installer_map[platform])) as f:
        for line in f.readlines():
            if "chmod +x" in line:
                benchmark_command = line.replace("chmod +x", '').rstrip().lstrip()
                print("Executable found in {} as {}".format(installer_map[platform], benchmark_command))

    file_inplace_replace(file_path=target_implementation_file,
                         search_string="PUT_COMMAND_HERE",
                         replace_string=f"./{benchmark_command}")


def create_info_file(target_dir, test_definition_xml, default_settings_file):
    """
    A function which creates an OpenForBC information file by getting the info from the test-definition.xml file.
    Title and Description are the sensitive tokens.
    """
    info_section = test_definition_xml.getElementsByTagName('TestInformation')[0]
    info_benchmark_name = info_section.getElementsByTagName('Title')[0].firstChild.nodeValue
    info_benchmark_description = info_section.getElementsByTagName('Description')[0].firstChild.nodeValue

    target_benchmark_info_file = os.path.join(target_dir, "benchmark_info.json")
    cp(benchmark_info_template, target_benchmark_info_file)

    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_NAME_HERE",
                         replace_string=info_benchmark_name)
    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_DESCRIPTION_HERE",
                         replace_string=info_benchmark_description)
    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_DEFAULT_SETTINGS_HERE",
                         replace_string=default_settings_file)


def get_related_platform(xml_package):
    """
    Utility to get the platform name for a download descriptor.
    """
    related_platform = None

    if xml_package.getElementsByTagName('PlatformSpecific'):
        platform_string = xml_package.getElementsByTagName('PlatformSpecific')[0].firstChild.nodeValue
        platform_string = platform_string.lower()

        if "Linux" in platform_string:
            related_platform = "linux"
        elif "macos" in platform_string:
            related_platform = "darwin"
        elif "windows" in platform_string:
            related_platform = "windows"

    return related_platform


def download_packages(bench_path, target_dir):
    """
    A function which downloads the required software as described in the downloads.xml file.
    It verifies the checksums afterwards. The downloads are listed in the form:

    <PhoronixTestSuite>
        <Downloads>
            <Package>
            <URL>http://something.tar.xz</URL>
            <MD5>54202a002878e4d0877e6e84c54202a0</MD5>
            <SHA256>9810c8fd3afd35b4755c2a46f14fc66e2b9199c22e46a5946123c9250f2d1ccd</SHA256>
            <FileName>something.tar.xz</FileName>
            <FileSize>452155436</FileSize>
            <PlatformSpecific>Windows</PlatformSpecific>
            </Package>
        </Downloads>
    </PhoronixTestSuite>

    """
    downloads_xml_path = os.path.join(bench_path, "downloads.xml")
    downloads_xml = minidom.parse(downloads_xml_path)
    packages_list = downloads_xml.getElementsByTagName('Package')

    for package in packages_list:
        urls = package.getElementsByTagName('URL')[0].firstChild.nodeValue.split(',')
        filename = package.getElementsByTagName('FileName')[0].firstChild.nodeValue

        try:
            md5 = package.getElementsByTagName('MD5')[0].firstChild.nodeValue
            print("Downloading {} (md5:{})".format(filename, md5))
        except Exception:
            md5 = None

            try:
                sha256 = package.getElementsByTagName('SHA256')[0].firstChild.nodeValue
                print("Downloading {} (sha256:{})".format(filename, sha256))
            except Exception:
                sha256 = None

                try:
                    size = package.getElementsByTagName('FileSize')[0].firstChild.nodeValue
                    print("Downloading {} (size:{})".format(filename, size))
                except Exception:
                    size = None

        target_file = os.path.join(target_dir, filename)
        platform_specific = get_related_platform(xml_package=package)
        if not platform_specific:
            should_download = True
        else:
            should_download = platform_specific == platform
            if not should_download:
                print(f'Skipping file {filename} since not required for this platform.')

        if os.path.isfile(target_file):
            with open(target_file, 'rb') as f:
                if md5:
                    if hashlib.md5(f.read()).hexdigest() == md5:
                        print("Already downloaded. Skipping.")
                        should_download = False
                elif sha256:
                    if hashlib.sha256(f.read()).hexdigest() == sha256:
                        print("Already downloaded. Skipping.")
                        should_download = False
                elif size:
                    if os.path.getsize(target_file) == size:
                        print("Already downloaded. Skipping.")
                        should_download = False
                else:
                    os.remove(target_file)

        if should_download:
            for url in urls:
                print(url)
                try:
                    opener = urllib.request.URLopener()
                    opener.addheader('User-Agent', 'Mozilla/5.0')
                    filename, _ = opener.retrieve(url, target_file, ProgressBar())
                    verified = False
                    if md5:
                        if hashlib.md5(open(target_file, 'rb').read()).hexdigest() == md5:
                            verified = True
                    elif sha256:
                        if hashlib.sha256(open(target_file, 'rb').read()).hexdigest() == sha256:
                            verified = True
                    elif size:
                        if os.path.getsize(target_file) == size:
                            verified = True
                    else:
                        verified = False

                    if verified:
                        break
                    else:
                        print("Wrong checksum. Trying from another source.")
                        os.remove(target_file)
                except Exception:
                    traceback.print_exc()
                    if url == urls[-1]:
                        raise Exception("None of the provided URLs works.")


def install_executable(target_dir):
    """
    A function to execute the phoronix setup script.
    """
    if os.path.isfile(os.path.join(target_dir, installer_map[platform])):
        cmd = ["bash", installer_map[platform]]
        my_env = os.environ.copy()
        my_env["HOME"] = target_dir

        # from https://gist.github.com/phizaz/e81d3d362e89bc68055cfcd670d44e9b
        with pipe() as (r, w):
            with subprocess.Popen(cmd, stdout=w, stderr=w, cwd=target_dir, env=my_env) as p:
                while p.poll() is None:
                    while len(select([r], [], [], 0)[0]) > 0:
                        buf = os.read(r, 1024)
                        print(buf.decode('utf-8'), end='')
    else:
        raise Exception(f"The current platform ({platform}) is not supported by this benchmark.")


def phoronix_install(benchmark_name, benchmark_v=None): # noqa: C901
    if phoronix_exists(benchmark_name, benchmark_v):
        if not benchmark_v:
            benchmark_v = list(bench_dict[benchmark_name]['versions'].keys())[-1]

        bench_path = os.path.join(bench_root_path, "{}-{}".format(benchmark_name, benchmark_v))

        test_definition_xml = minidom.parse(os.path.join(bench_path, "test-definition.xml"))
        settings_list = test_definition_xml.getElementsByTagName('Entry')

        safe_mkdir(install_dir)

        target_dir = os.path.join(install_dir, 'phoronix-{}-{}'.format(benchmark_name, benchmark_v))

        safe_mkdir(target_dir)

        settings_dir = os.path.join(target_dir, "settings")
        default_settings_file = convert_settings(settings_list=settings_list, settings_dir=settings_dir)

        install_installers(bench_path=bench_path, target_dir=target_dir)

        create_implementation_file(target_dir=target_dir, benchmark_name=benchmark_name, benchmark_path=bench_path)

        create_info_file(target_dir=target_dir,
                         test_definition_xml=test_definition_xml,
                         default_settings_file=default_settings_file)

        download_packages(bench_path=bench_path, target_dir=target_dir)

        install_executable(target_dir=target_dir)
    else:
        raise Exception(f"The required benchmark {benchmark_name} @ {benchmark_v} doesn't exist.")


if __name__ == "__main__":
    phoronix_init()
    # phoronix_install("astcenc", "1.1.0")
    # phoronix_install("cinebench")
    # phoronix_list("cinebench")
    phoronix_list("astcenc")
    # phoronix_list()
