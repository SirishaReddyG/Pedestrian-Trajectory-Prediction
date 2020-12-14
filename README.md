Code of the Relational Architecture for Pedestrian Trajectory Prediction

Environment:

pip install numpy==1.18.1
pip install torch==1.7.0
pip install pyyaml=5.3.1
pip install tqdm=4.45.0

Train:

The Default settings are to train on ETH-Univ dataset.
Data cache and models will be stored in the subdirectory "./output/eth/" by default.
This implementation is on GPU.

Line to Run:

python trainval.py --test_set <dataset to evaluate> --start_test <epoch to start test>

The datasets are selected on arguments '--test_set'. Five datasets in ETH/UCY including [eth,
hotel, zara1, zara2, univ].

Configuration files are also created after the first run, arguments could be modified through configuration files or command line.
Priority: command line > configuration files > default values in script.

Example:

This command is to train model for ETH-hotel and start test at epoch 10. 
For different dataset, change 'hotel' to other datasets named in the last section.

python trainval.py --test_set hotel --start_test 50

During training, the model for Best FDE on the corresponding test dataset would be record.
