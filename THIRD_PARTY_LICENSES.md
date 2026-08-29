# Third-party licenses

Rosace itself is licensed under the GNU General Public License v3 (see
[LICENSE](LICENSE)). This file lists code incorporated into Rosace that
originates from another project, under that project's own license.

## PANOPTES/POCS (MIT)

`rosace/rosace/hardware_indi/wrapper/` and most of
`rosace/rosace/hardware_indi/devices/` (INDI client/device wrapper classes,
including the generic mount/camera/focuser/filter-wheel device classes)
originate from the [PANOPTES/POCS](https://github.com/panoptes/POCS)
observatory control software — a project whose approach to observatory
automation shaped a good part of Rosace's own thinking, not just a source of
boilerplate. The POCS-specific configuration loader and MongoDB-backed
database layer it used to pull in have been removed, since Rosace has its
own configuration (`config.py`) and databases (`database.py`, `targets.py`);
everything else in those files keeps its original structure and license.

Not from POCS — written for Rosace, since POCS has no concept of a
spectrograph: `hardware_indi/devices/spectro_Alpy600.py`,
`usis_spectroscope.py`, and `spectroscope_placeholder.py`.

POCS's own license (MIT), reproduced in full below, copied verbatim from
<https://github.com/panoptes/POCS/blob/develop/LICENSE.txt>:

```
The MIT License (MIT)

Copyright (c) 2014-2020 Project PANOPTES

Copyright 2016 Google Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
