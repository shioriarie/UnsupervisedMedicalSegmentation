FROM continuumio/miniconda3:latest

# Set the working directory inside the container
WORKDIR /app

# Copy the Conda environment file to the container
COPY environment.yml .

# Create the Conda environment
RUN conda env create -f environment.yml

# Activate the Conda environment
RUN echo "source activate cuts" > ~/.bashrc
ENV PATH /opt/conda/envs/cuts/bin:$PATH

# Install additional packages with pip
RUN pip install -U phate && \
    pip install git+https://github.com/KrishnaswamyLab/CATCH && \
    pip install opencv-python && \
    pip install sewar && \
    pip install monai && \
    pip install nibabel

# (Optional) Install additional packages for STEGO
RUN pip install omegaconf && \
    pip install wget && \
    pip install torchmetrics && \
    pip install tensorboard && \
    pip install pytorch-lightning==1.9 && \
    pip install azureml && \
    pip install azureml.core

# Copy the rest of your application files to the container
COPY . .

# Set the command to run your application
CMD ["python"]
