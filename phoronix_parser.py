#!/usr/bin/env python3

import git
import os
import glob
from xml.dom import minidom
import requests
import hashlib
from shutil import copy2 as cp, COPY_BUFSIZE
import stat
from sys import platform
import subprocess
from select import select
from contextlib import contextmanager
import json
import fileinput
import traceback
import progressbar

REMOTE_BENCH_ROOT_PATH = os.path.join("ob-cache", "test-profiles", "pts")
file_dir = os.path.dirname(os.path.abspath(__file__))
clone_dir = os.path.join(file_dir, "phoronix-benchs")
bench_root_path = os.path.join(clone_dir, REMOTE_BENCH_ROOT_PATH)
install_dir = os.path.join(file_dir, "phoronix-converted")
benchmark_info_template = os.path.join(file_dir, "phoronix_benchmark.json.template")
setup_template = os.path.join(file_dir, "phoronix_setup.sh.template")

installer_map = {"linux": "install.sh",
                 "linux2": "install.sh",
                 "darwin": "install_macosx.sh",
                 "windows": "install_windows.sh"}
bench_dict = {}


@contextmanager
def pipe():
    r, w = os.pipe()
    yield r, w
    os.close(r)
    os.close(w)


class ProgressBar():
    def __init__(self, total_size):
        self.pbar = None
        self.total_size = total_size if total_size > 0 else progressbar.UnknownLength
        self.widgets = [progressbar.Percentage() if total_size else ' ',
                        progressbar.Bar(), ' ',
                        progressbar.FileTransferSpeed(), ' ',
                        ' (', progressbar.ETA(), ') ']

    def call(self, block_num, block_size):
        if not self.pbar:
            self.pbar = progressbar.ProgressBar(maxval=self.total_size,
                                                widgets=self.widgets)
            self.pbar.start()

        downloaded = block_num * block_size

        if self.total_size == progressbar.UnknownLength:
            self.pbar.update(downloaded)
        else:
            if downloaded < self.total_size:
                self.pbar.update(downloaded)
            else:
                self.pbar.finish()


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


def phoronix_list(benchmark_name=None, plat=None):
    """
    Function capable of listing all the versions of a given benchmark.
    """
    if plat is None:
        plat = platform
    if benchmark_name is None or not benchmark_name:
        if not bench_dict:
            generate_dict()
        for bench_name, bench_data in bench_dict.items():
            for v, p in bench_data['versions'].items():
                if plat in p:
                    print(f"{bench_name} @ {v} [{plat}]")
    else:
        local_benchmark_repo = os.path.join(clone_dir, REMOTE_BENCH_ROOT_PATH, benchmark_name)
        results = glob.glob(os.path.join(bench_root_path, local_benchmark_repo + "*"))
        if results:
            if not bench_dict:
                generate_dict()
            for v, p in bench_dict[benchmark_name]['versions'].items():
                if plat in p:
                    print(f"{benchmark_name} @ {v} [{plat}]")
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


def mycopyfileobj(fsrc, fdst, length=0, total_size=0, prog_bar: ProgressBar = None):
    """copy data from file-like object fsrc to file-like object fdst"""
    # Localize variable access to minimize overhead.
    if not prog_bar:
        prog_bar = ProgressBar(total_size)
    if not length:
        length = COPY_BUFSIZE
    fsrc_read = fsrc.read
    fdst_write = fdst.write
    block_num = 0
    while True:
        block_num += 1
        buf = fsrc_read(length)
        if not buf:
            break
        fdst_write(buf)
        prog_bar.call(block_num=block_num, block_size=length)


def download_file(url, target_filename):
    with requests.get(url, stream=True) as r:
        try:
            total_size = int(r.headers.get('Content-Length'))
        except Exception:
            total_size = 0
        with open(target_filename, 'wb') as f:
            mycopyfileobj(r.raw, f, total_size=total_size)


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
    if len(settings_list) == 0:
        name = 'unique_preset'
        args = 'no_setting_specified'
        dict = {"args": args}

        with open(os.path.join(settings_dir, f"preset-{name}.json"), 'w+') as outfile:
            json.dump(dict, outfile)
            if save_default_settings:
                default_settings_file = f"preset-{name}.json"
                save_default_settings = False
    else:
        for setting in settings_list:
            if len(setting.getElementsByTagName('Name')) != 0:
                name = setting.getElementsByTagName('Name')[0].firstChild.nodeValue.lower()
            if len(setting.getElementsByTagName('Value')) != 0:
                args = setting.getElementsByTagName('Value')[0].firstChild.nodeValue

            dict = {"args": args}
            with open(os.path.join(settings_dir, f"preset-{name}.json"), 'w+') as outfile:
                json.dump(dict, outfile)
                if save_default_settings:
                    default_settings_file = f"preset-{name}.json"
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


def create_setup_file(target_dir, benchmark_name):
    """
    A function which creates a setup file from a template.
    """
    from os import chmod, stat
    from stat import S_IEXEC

    target_setup_file = os.path.join(target_dir, "setup.sh")
    cp(setup_template, target_setup_file)
    file_stat = stat(target_setup_file)
    chmod(target_setup_file, file_stat.st_mode | S_IEXEC)
    file_inplace_replace(
        file_path=target_setup_file,
        search_string="BENCHMARK_NAME",
        replace_string=benchmark_name,
    )


def create_info_file(target_dir, test_definition_xml, results_definition_xml, default_settings_file, benchmark_name):
    """
    A function which creates an OpenForBC benchmark by getting the info from the test-definition.xml file and
    by defining the run command, i.e. the script to be executed in order to run the benchmark.
    Title and Description are the sensitive tokens.
    Furthermore, this function takes informations from results-definition.xml about benchamrk results
    parsing in order to put them inside the benchmark.json file.
    """
    info_section = test_definition_xml.getElementsByTagName('TestInformation')[0]
    info_benchmark_name = info_section.getElementsByTagName('Title')[0].firstChild.nodeValue
    info_benchmark_description = info_section.getElementsByTagName('Description')[0].firstChild.nodeValue
    benchmark_run_command = "./" + benchmark_name

    target_benchmark_info_file = os.path.join(target_dir, "benchmark.json")
    cp(benchmark_info_template, target_benchmark_info_file)

    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_NAME_HERE",
                         replace_string=info_benchmark_name)
    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_DESCRIPTION_HERE",
                         replace_string=info_benchmark_description)
    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_DEFAULT_PRESETS_HERE",
                         replace_string=default_settings_file)
    file_inplace_replace(file_path=target_benchmark_info_file,
                         search_string="PUT_RUN_COMMAND_HERE",
                         replace_string=benchmark_run_command)

    # RESULTS PARSING
    # Results can be represented with two different tag inside the results-definition.xml,
    # <ResultParser> or <SystemMonitor>.

    regex = "(.*)"

    if len(results_definition_xml.getElementsByTagName('ResultsParser')) != 0:
        results_parser = results_definition_xml.getElementsByTagName('ResultsParser')
        results_dict = {} # dict when I put all the statistics to parse
        for node in results_parser:
            stat = node.getElementsByTagName('OutputTemplate')[0].firstChild.nodeValue
            if len(node.getElementsByTagName('ArgumentsDescription')) != 0:
                stat_name = node.getElementsByTagName('ArgumentsDescription')[0].firstChild.nodeValue
            else:
                stat_name = "results"

            stat = stat.replace("#_RESULT_#", regex)
            mini_dict = {}
            mini_dict['regex'] = stat
            results_dict[stat_name] = mini_dict
            results_string = json.dumps(results_dict)
    else:
        results_parser = results_definition_xml.getElementsByTagName('SystemMonitor')
        results_dict = {} # dict when I put all the statistics to parse
        for node in results_parser:
            stat = node.getElementsByTagName('Sensor')[0].firstChild.nodeValue
            stat_name = "results"

            stat = stat.replace("#_RESULT_#", regex)
            mini_dict = {}
            mini_dict['regex'] = stat
            results_dict[stat_name] = mini_dict
            results_string = json.dumps(results_dict)

    file_inplace_replace(file_path=target_benchmark_info_file, search_string="PUT_STATS_HERE",
                         replace_string=results_string)


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


def get_download_packages(downloads_xml_path):
    """
    A function which obtains the downloads info as described in the downloads.xml file.
    The output is either a dictionary or a json file.
    The downloads in the downloads.xml file are listed in the form:

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
    downloads = []

    if os.path.exists(downloads_xml_path):
        downloads_xml = minidom.parse(downloads_xml_path)
        packages_list = downloads_xml.getElementsByTagName('Package')

        for package in packages_list:
            package_dict = {}
            package_dict["urls"] = package.getElementsByTagName('URL')[0].firstChild.nodeValue.split(',')
            filename = package.getElementsByTagName('FileName')[0].firstChild.nodeValue
            package_dict["filename"] = filename

            package_dict["platform"] = get_related_platform(xml_package=package)

            try:
                md5 = package.getElementsByTagName('MD5')[0].firstChild.nodeValue
                package_dict["md5"] = md5
            except Exception:
                md5 = None

                try:
                    sha256 = package.getElementsByTagName('SHA256')[0].firstChild.nodeValue
                    package_dict["sha256"] = sha256
                except Exception:
                    sha256 = None

                    try:
                        size = int(package.getElementsByTagName("FileSize")[
                            0
                        ].firstChild.nodeValue)
                    except Exception:
                        size = None

                    package_dict["size"] = size

            downloads.append(package_dict)

        return downloads
    else:
        return None


def download_packages(bench_path, target_dir):
    """
    A function which downloads the required software as described by get_download_packages().
    It verifies the checksums afterwards.
    """
    downloads_xml_path = os.path.join(bench_path, "downloads.xml")
    packages = get_download_packages(downloads_xml_path=downloads_xml_path)
    if packages:
        for package in packages:
            urls = package["urls"]
            filename = package["filename"]

            try:
                md5 = package["md5"]
                print("Downloading {} (md5:{})".format(filename, md5))
            except Exception:
                md5 = None

                try:
                    sha256 = package["sha256"]
                    print("Downloading {} (sha256:{})".format(filename, sha256))
                except Exception:
                    sha256 = None

                    try:
                        size = package["size"]
                        print("Downloading {} (size:{})".format(filename, size))
                    except Exception:
                        size = None

            target_file = os.path.join(target_dir, filename)
            platform_specific = package["platform"]
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
                downloaded = False
                for url in urls:
                    print(url)
                    try:
                        download_file(url=url, target_filename=target_file)
                    except Exception:
                        traceback.print_exc()
                        continue

                    hash = md5 or sha256
                    hash_fn = hashlib.md5 if md5 else hashlib.sha256 if sha256 else None
                    if hash_fn:
                        actual_hash = hash_fn(
                            open(target_file, "rb").read()
                        ).hexdigest()
                        verified = actual_hash == hash
                        if not verified:
                            print(
                                "Got wrong checksum downloading "
                                f"{filename} from {url}:\n"
                                f"\t{hash} expected, but got\n"
                                f"\t{actual_hash} instead."
                            )
                    elif size:
                        print(f"No hash specified, checking file size instead.")
                        actual_size = os.path.getsize(target_file)
                        verified = actual_size == size
                        if not verified:
                            print(
                                f"Got wrong filesize downloading {filename} from {url}: "
                                f"{actual_size} != {size} (expected)."
                            )
                    else:
                        verified = False

                    if not verified:
                        print(f"File {target_file} will now be removed.")
                        os.remove(target_file)
                        continue

                    downloaded = True
                    break

                if not downloaded:
                    raise Exception(f"Could not download {filename} from any of specified URLs")

    return


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


def phoronix_install(benchmark_name, benchmark_v=None):
    if phoronix_exists(benchmark_name, benchmark_v):
        if not benchmark_v:
            benchmark_v = list(bench_dict[benchmark_name]['versions'].keys())[-1]
            print(f"Benchmark version not specified, defaulting to latest ({benchmark_v})")
        else:
            print(f"Selected benchmark version: {benchmark_v}")
        bench_path = os.path.join(bench_root_path, "{}-{}".format(benchmark_name, benchmark_v))

        test_definition_xml = minidom.parse(os.path.join(bench_path, "test-definition.xml"))
        results_definition_xml = minidom.parse(os.path.join(bench_path, "results-definition.xml"))
        settings_list = test_definition_xml.getElementsByTagName('Entry')

        safe_mkdir(install_dir)

        target_dir = os.path.join(install_dir, 'phoronix-{}-{}'.format(benchmark_name, benchmark_v))

        safe_mkdir(target_dir)

        settings_dir = os.path.join(target_dir, "presets")
        default_settings_file = convert_settings(settings_list=settings_list, settings_dir=settings_dir)

        install_installers(bench_path=bench_path, target_dir=target_dir)

        create_setup_file(target_dir=target_dir, benchmark_name=benchmark_name)

        create_info_file(target_dir=target_dir,
                         test_definition_xml=test_definition_xml,
                         results_definition_xml=results_definition_xml,
                         default_settings_file=default_settings_file,
                         benchmark_name=benchmark_name)

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
