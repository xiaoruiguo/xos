export XOS_DIR=/opt/xos
nohup python vtn-synchronizer.py  -C $XOS_DIR/observers/vtn/vtn_synchronizer_config > /dev/null 2>&1 &
