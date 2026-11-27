FROM --platform=linux/amd64 ubuntu:22.04 AS base

SHELL ["/bin/bash", "-c"]

ENV project=attendee
ENV cwd=/$project

WORKDIR $cwd

ARG DEBIAN_FRONTEND=noninteractive

# Runtime dependencies only. Compilers and headers belong in the deps stage below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libdbus-1-3 \
    libgbm1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libpq5 \
    libx11-xcb1 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-shape0 \
    libxcb-shm0 \
    libxcb-xfixes0 \
    libxcb-xtest0 \
    libgl1-mesa-dri \
    libxfixes3 \
    procps \
    python3-pip \
    tar \
    unzip \
    # Chrome dependencies
    xvfb xauth x11-xkb-utils xfonts-100dpi xfonts-75dpi xfonts-scalable xfonts-cyrillic x11-apps libvulkan1 fonts-liberation xdg-utils wget \
    # ALSA
    libasound2 libasound2-plugins alsa-utils alsa-oss \
    # Pulseaudio
    pulseaudio pulseaudio-utils ffmpeg \
    xterm \
    xclip \
    # Needed so that webpage streaming using pyav will work.
    libavdevice58 \
    # GStreamer, plus the GObject bindings used to drive it from python
    gstreamer1.0-alsa gstreamer1.0-tools gstreamer1.0-x gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
    python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    && rm -rf /var/lib/apt/lists/*

# Install a specific version of Chrome. The hash is checked because this is a mirror.
RUN wget --progress=dot:giga --timeout=30 --tries=3 -O /tmp/google-chrome-stable_134.0.6998.88-1_amd64.deb https://build-assets.attendee.dev/google-chrome/pool/main/g/google-chrome-stable/google-chrome-stable_134.0.6998.88-1_amd64.deb \
    && echo "df557edb3d24d8dcaff9557d80733b42afb6626685200d3f34a3b6f528065cad  /tmp/google-chrome-stable_134.0.6998.88-1_amd64.deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/google-chrome-stable_134.0.6998.88-1_amd64.deb \
    && rm -rf /tmp/google-chrome-stable_134.0.6998.88-1_amd64.deb /var/lib/apt/lists/*

# Install a specific version of ChromeDriver.
RUN wget -q https://storage.googleapis.com/chrome-for-testing-public/134.0.6998.88/linux64/chromedriver-linux64.zip \
    && echo "58df717d51484b9f3ac188af5231cdc77255daa72d0b2b86481bee54e398ce2f  chromedriver-linux64.zip" | sha256sum -c - \
    && unzip chromedriver-linux64.zip \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf chromedriver-linux64 chromedriver-linux64.zip

# Alias python3 to python
RUN ln -s /usr/bin/python3 /usr/bin/python

ENV TINI_VERSION=v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN echo "93dcc18adc78c65a028a84799ecf8ad40c936fdfc5f2a57b1acda5a8117fa82c  /tini" | sha256sum -c -
RUN chmod +x /tini

# Compiles the python packages. None of this stage's system packages reach the final image.
FROM base AS deps

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkgconf python3-dev libpq-dev libavdevice-dev

# av is built against the system ffmpeg so that webpage streaming using pyav will work.
RUN pip install --no-cache-dir --no-binary av "av==12.0.0"

# Copy only requirements.txt first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Not used at runtime. ruff stays in requirements.txt because CI lints with it.
RUN pip uninstall -y ruff Cython gdown

FROM base AS build

COPY --from=deps /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash app

# Workdir owned by app in one shot during copy
ENV project=attendee
ENV cwd=/$project
WORKDIR $cwd

# Copy only what you need; set ownership/perm at copy time
COPY --chown=app:app --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=app:app . .

# Make STATIC_ROOT writeable for the non-root user so collectstatic can run at startup
RUN mkdir -p "$cwd/staticfiles" && chown -R app:app "$cwd/staticfiles"

# We want the app to be able to dynamically set the chrome policies file.
# However, chrome will load the file from a hardcoded path in a directory that the app cannot write to.
# Therefore, we create a symlink at that path that points to a file in /tmp which the app can write to.
RUN mkdir -p /etc/opt/chrome/policies/managed \
  && ln -s /tmp/attendee-chrome-policies.json /etc/opt/chrome/policies/managed/attendee-chrome-policies.json

# Switch to non-root AFTER copies to avoid permission flakiness
USER app

# Use tini + entrypoint; CMD can be overridden by compose
ENTRYPOINT ["/tini","--","/usr/local/bin/entrypoint.sh"]
CMD ["bash"]