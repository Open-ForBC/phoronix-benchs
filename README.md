# phoronix-benchs
This repository serves as a translation layer between phoronix benchmark definitions and the Open ForBC Benchmark toolset.

## Working modes
This tool provides two working modes: `list` and `install`.

### `list` working mode
The `list` working mode is aimed at providing a comprehensive way to traverse the huge set of tests provided by the Phoronix suite.

```console
foo@bar:~$ ./phoro2o4bc list
Origin already set up.
ai-benchmark @ 1.0.1 [darwin]
appleseed @ 1.0.1 [darwin]
asmfish @ 1.1.0 [darwin]
[...]
```

By default the command lists the available benchmarks for the platform the command is running on (`darwin`, `windows` or `linux[2]`).

#### Benchmark-versions listing
Appending the name of a benchmark after a list command allows one to see merely the available versions of the specified piece of software:

```console
foo@bar:~$ ./phoro2o4bc list astcenc
Origin already set up.
astcenc @ 1.0.0 [darwin]
astcenc @ 1.0.1 [darwin]
astcenc @ 1.0.2 [darwin]
astcenc @ 1.1.0 [darwin]
astcenc @ 1.2.0 [darwin]
```

#### Platform-specific listing
The `-p` or `--platform` flag can be used to specify another platform:

```console
foo@bar:~$ ./phoro2o4bc list -p windows
Origin already set up.
aircrack-ng @ 1.1.1 [windows]
aircrack-ng @ 1.1.2 [windows]
aircrack-ng @ 1.2.0 [windows]
aircrack-ng @ 1.2.1 [windows]
aom-av1 @ 2.0.2 [windows]
aom-av1 @ 2.1.1 [windows]
[...]
```

```console
foo@bar:~$ ./phoro2o4bc list --platform linux
Origin already set up.
ai-benchmark @ 1.0.0 [linux]
ai-benchmark @ 1.0.1 [linux]
aio-stress @ 1.1.1 [linux]
aio-stress @ 1.1.2 [linux]
aircrack-ng @ 1.1.1 [linux]
aircrack-ng @ 1.1.2 [linux]
[...]
```

:::success
This option can be combined with the selection of a benchmark name in order to list the versions of a given benchmark which are available for a specific platform.
:::

### `install` working mode
This mode is capable of installing a Phoronix benchmark with ease, converting the upstream definition of the benchmark into an Open ForBC Benchmark -compatible one.

It requires a mandatory positional argument defining the benchmark name of the benchmark one wants to install.

```console
foo@bar:~$ ./phoro2o4bc install astcenc
Origin already set up.
Benchmark version not specified, defaulting to latest (1.2.0)
[...]
```

Optionally, the second positional argument enables the cherry-picking of a specific version amongst those available.

```console
foo@bar:~$ ./phoro2o4bc install astcenc 1.0.1
Origin already set up.
Selected benchmark version: 1.0.1
[...]
```

## How to test a benchmark locally
Once the benchmark is installed it can be easily run using Open ForBC Benchmark, since the format is compliant with that package.

In case one wants to run the benchmark "standalone", to test its functionality, the procedure is performing a `cd` towards `./phoronix-converted/phoronix-<benchmark name>-<benchmark version>`.
As an example, with astcenc 1.0.1:

```console
foo@bar:~$ cd ./phoronix-converted/phoronix-astcenc-1.0.1
```

The benchmark executable name may vary, but it should be a bash script lacking the `.sh` extension.

```console
foo@bar:~$ ls -1rt./phoronix-converted/phoronix-astcenc-1.0.1
sample-1.png
sample-2.png
sample-3.png
sample-4.png
astcenc-2.0-macos-x64
settings
install.sh
install_macosx.sh
install_windows.sh
implementation.py
benchmark_info.json
png-samples-1.tar.xz
astc-encoder-2.0.tar.gz
astcenc-2.0-macos-x64.zip
astcenc # <- THAT'S THE EXECUTABLE!
1.png
log
```

The CLI arguments should be read manually from one of the setting jsons available at `./settings` after the conversion.

```console
foo@bar:~$ less ./phoronix-converted/phoronix-astcenc-1.0.1/settings/settings-fast.json
{"cli_args": "-fast"}
```

Before running the benchmark set the `LOG_FILE` environment variable to a valid target by running:

```console
foo@bar:~$ export LOG_FILE=./log.txt
```

Run the benchmark from its root dir via:

```console
foo@bar:~$ ./astcenc -fast
```

And check for any output in the `LOG_FILE`-pointed target.