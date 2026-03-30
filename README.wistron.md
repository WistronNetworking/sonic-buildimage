# 🚀 master-mrvl-prestera-amazon: Development Overview & Technical Log ✨

> **Quick Summary:** This branch was forked from `master-mrvl-prestera` to serve as a dedicated environment for MRVL SAI. Our goal is to align with vendor requirements while maintaining full compatibility with the base architecture. 🌈

---

## 🧐 Rationale & Motivation
This branch was initialized to address specific MRVL SAI version that necessitate a departure from the current `master-mrvl-prestera` baseline.

---

## ⚖️ Technical Delta (master-mrvl-prestera vs. master-mrvl-prestera-amazon)
Here is a high-level comparison of the MRVL SAI package introduced in this branch.

| MRVL SAI Package(mrvllibsai) | master-mrvl-prestera | master-mrvl-prestera-amazon |
| :--- | :--- | :--- |
| **SAI Version** | 1.15.1-1 | 1.16.1-3 |
| **XPS commit** | 558fa4f2 | d3d5a5f5 |
| **SAI commit** | 190b2f41 | d3d5a5f5 |
| **CPSS commit** | 01af8e8f3f | ad36e65518 |
| **Opencomputeproject/SAI commit** | f214ade | 23d8579 |
| **Release** | P5.0.0 | P6.0.1 |
| **EzB** | 1.10 | 1.12 |
| **Date** | Mon 20 Jan 2025 09:08:27 AM UTC | Sat Nov 15 18:14:01 UTC 2025 |

---

## 💌 Developer’s Note
> "Code is like poetry; may yours always be elegant and bug-free! (づ｡◕‿‿◕｡)づ"

Please ensure all commits follow the project's **Conventional Commits** standard. If you encounter any integration conflicts, feel free to reach out for a sync-up! 🚀

---

<BR><BR><BR><BR>

# 🚀 SONiC Image Build Instructions

## Description

Following are the instructions on how to build an ONIE compatible
network operating system (NOS) installer image for Wistron network switches.

## Usage

To build SONiC installer image, run the following commands:

```shell
# Enter the source directory
cd sonic-buildimage

# (Optional) Checkout a specific branch. By default, it uses master branch.
git checkout master-mrvl-prestera-amazon

# Execute make init once after cloning the repo,
# or after fetching remote repo with submodule updates
make init

# Execute patch for platform specific modifications and issue fixing.
for file in 0001-sonic-kernel-modification-for-wistron.patch \
    0001-fix-sonic-swss-build-error.patch; do \
    patch -p1 < $file
done

# Execute make configure once to configure ASIC
make configure PLATFORM=marvell-prestera PLATFORM_ARCH=arm64

# Build SONiC image with 4 jobs in parallel.
# Note: You can set this higher, but 4 is a good number for most cases
#       and is well-tested.
make NOBUSTER=1 NOBULLSEYE=1 SONIC_BUILD_JOBS=3 target/sonic-marvell-prestera-arm64.bin
```
