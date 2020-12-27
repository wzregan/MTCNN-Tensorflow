from mtcnn.layer import *
from mtcnn import config
from Data.fddb.load import *
from Data.aflw.load import *

from tqdm import tqdm
from util import overlap
import sys
import numpy as np

class ModelHelper:
    def __init__(self, save_path, sess: tf.Session, step=10):
        self.sess = sess
        self.saver = tf.train.Saver()
        self.every_step = step
        self.save_path = save_path
        self.latest = tf.train.latest_checkpoint(self.save_path)
        self.log_opt = None

    def reload(self):
        if self.latest is not None:
            self.saver.restore(self.sess, self.latest)
            return int(self.latest.split('-')[-1])
        return 0

    def save(self, cur_step):
        if cur_step == 0 or cur_step % self.every_step == 0:
            self.saver.save(self.sess, self.save_path, global_step=cur_step)


def Pnet_factory():
    with tf.name_scope('pnet'):
        x_input = tf.placeholder(shape=config.pnet_input_size, dtype=tf.float32)

        conv1_out = add_conv(x_input, kernel_shape=[3, 3, 3, 28], name_scope='CONV_1', activation=prelu, mode='VALID')

        pool2_out = max_pool(conv1_out, size=1, stride=2, name_scope='MP_2', mode='SAME')

        conv3_out = add_conv(pool2_out, kernel_shape=[3, 3, 28, 48], name_scope='CONV_3', activation=prelu,
                             mode='VALID')

        conv4_out = add_conv(conv3_out, kernel_shape=[3, 3, 48, 64], name_scope='CONV_4', activation=prelu,
                             mode='VALID')

        face_classfier = add_conv(conv4_out, kernel_shape=[1, 1, 32, 2], name_scope='CONV_5', activation=tf.nn.softmax,
                                  mode='VALID')

        bound_reg = add_conv(conv4_out, kernel_shape=[1, 1, 32, 4], name_scope='CONV_6', activation=None, mode='VALID')

        landmark_reg = add_conv(conv4_out, kernel_shape=[1, 1, 32, 10], name_scope='CONV_7', activation=None,
                                mode='VALID')

        return x_input, tf.squeeze(face_classfier, axis=[1, 2]), tf.squeeze(bound_reg, axis=[1, 2]), tf.squeeze(
            landmark_reg, axis=[1, 2])


def Rnet_factory():
    with tf.name_scope('Rnet'):
        x_input = tf.placeholder(shape=config.onet_input_size, dtype=tf.float32)

        conv1_out = add_conv(x_input, kernel_shape=[3, 3, 3, 28], name_scope='CONV_1', activation=prelu, mode='VALID')

        pool2_out = max_pool(conv1_out, size=1, stride=2, name_scope='MP_2', mode='SAME')

        conv3_out = add_conv(pool2_out, kernel_shape=[3, 3, 28, 48], name_scope='CONV_3', activation=prelu,
                             mode='VALID')

        conv4_out = add_conv(conv3_out, kernel_shape=[3, 3, 48, 64], name_scope='CONV_4', activation=prelu,
                             mode='VALID')

        conv4_flatten = add_flatten(conv4_out, 'fallten')

        feature = add_dense(inputs=conv4_flatten, units=128, activation=prelu, name_scope='FC_5')

        face_classfier = add_dense(inputs=feature, units=2, activation=tf.nn.softmax, name_scope='OUT_CLASSIFER')

        bound_reg = add_dense(inputs=feature, units=4, name_scope='OUT_REG')

        landmark_reg = add_dense(inputs=feature, units=4, name_scope='OUT_LANDMARK')

        return x_input, face_classfier, bound_reg, landmark_reg


def Onet_factory():
    with tf.name_scope('Onet'):
        x_input = tf.placeholder(shape=config.rnet_input_size, dtype=tf.float32)

        conv1_out = add_conv(x_input, kernel_shape=[3, 3, 3, 32], name_scope='CONV_1', activation=prelu, mode='VALID')

        pool2_out = max_pool(conv1_out, size=1, stride=2, name_scope='MP_2', mode='SAME')

        conv3_out = add_conv(pool2_out, kernel_shape=[3, 3, 32, 64], name_scope='CONV_3', activation=prelu,
                             mode='VALID')
        pool3_out = max_pool(conv3_out, size=1, stride=2, name_scope='MP_3', mode='SAME')

        conv4_out = add_conv(pool3_out, kernel_shape=[3, 3, 64, 64], name_scope='CONV_4', activation=prelu,
                             mode='VALID')
        pool4_out = max_pool(conv4_out, size=1, stride=2, name_scope='MP_4', mode='SAME')

        conv5_out = add_conv(pool4_out, kernel_shape=[3, 3, 64, 128], name_scope='CONV_5', activation=prelu,
                             mode='VALID')

        conv5_flatten = add_flatten(conv5_out, 'fallten')

        feature = add_dense(inputs=conv5_flatten, units=256, activation=prelu, name_scope='FC_5')

        face_classfier = add_dense(inputs=feature, units=2, name_scope='OUT_CLASSIFER')

        bound_reg = add_dense(inputs=feature, units=4, name_scope='OUT_REG')

        landmark_reg = add_dense(inputs=feature, units=10, name_scope='OUT_LANDMARK')
        return x_input, face_classfier, bound_reg, landmark_reg


def Classifier_loss(label, target, feature):
    mul_loss = tf.nn.softmax_cross_entropy_with_logits(logits=feature, labels=target)
    valid_index = tf.cast(tf.equal(label, 1), tf.int64)
    valid_number = tf.cast(valid_index, tf.float32)
    loss = tf.reduce_sum(tf.multiply(mul_loss, valid_number)) / tf.reduce_sum(valid_number)
    prob = tf.multiply(tf.squeeze(tf.slice(tf.nn.softmax(feature), [0, 1], [-1, 1])), valid_number)
    sel = tf.where(tf.equal(valid_index, 1))
    acc = tf.reduce_mean(
        tf.cast(tf.equal(tf.gather(tf.argmax(target, axis=1), sel), tf.gather(tf.argmax(feature, axis=1), sel)),
                dtype=tf.float32))
    return acc, prob, loss


def Bounding_box_loss(label, feature, y_bound):
    mul_loss = tf.reduce_sum(tf.square(feature - y_bound), axis=1)
    valid_index = tf.equal(label, 0)
    valid_number = tf.cast(valid_index, tf.float32)
    loss = tf.reduce_sum(tf.multiply(mul_loss, valid_number)) / tf.reduce_sum(valid_number)
    return loss


def Landmark_loss(label, feature, y_landmark):
    mul_loss = tf.reduce_sum(tf.square(feature - y_landmark), axis=1)
    valid_index = tf.equal(label, 2)
    valid_number = tf.cast(valid_index, tf.float32)
    loss = tf.reduce_sum(tf.multiply(mul_loss, valid_number)) / tf.reduce_sum(valid_number)
    return loss


class Pnet:
    def __init__(self, is_train=True):
        self.graph = tf.Graph()
        with self.graph.as_default():
            self.x_input, self.face_c, self.bound_r, self.landmakr_r = Pnet_factory()
            self.y_label = tf.placeholder(dtype=tf.float32, shape=[None, 2])
            self.y_bound = tf.placeholder(dtype=tf.float32, shape=[None, 4])
            self.land_mark = tf.placeholder(dtype=tf.float32, shape=[None, 10])
            self.label = tf.placeholder(dtype=tf.float32, shape=[None, ])
            self.acc, self.cls_prob, self.cls_loss = Classifier_loss(self.label, self.y_label, self.face_c)
            self.bound_loss = Bounding_box_loss(self.label, self.bound_r, self.y_bound)
            self.com_loss = self.cls_loss * 2 + self.bound_loss * 1
            if is_train:
                self.global_step = tf.Variable(0)
                self.add_step = tf.assign_add(self.global_step, 1)
                self.lr = tf.train.exponential_decay(learning_rate=0.01, global_step=self.global_step, decay_steps=1000,
                                                     decay_rate=0.5)
                self.train_opt = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.com_loss)
            self.sess = tf.Session()
            self.model_saver = ModelHelper(save_path='../../model/pnet/', sess=self.sess)

    def train(self):
        def merge(x_c_train, x_r_train, y_c_train, y_r_train):
            X = np.concatenate([x_c_train, x_r_train], axis=0)
            YC = np.concatenate([y_c_train, np.zeros(shape=[len(y_r_train), y_c_train.shape[1]])], axis=0)
            YR = np.concatenate([np.zeros(shape=[len(y_c_train), y_r_train.shape[1]]), y_r_train], axis=0)
            label = np.concatenate(
                [np.ones(shape=[len(x_c_train), ], dtype=int), np.zeros(shape=[len(x_r_train)], dtype=int)], axis=0)
            return X, YC, YR, label

        with self.graph.as_default():
            classifier = ImageDateSet(negative_dir='/home/dataset/FDDB/MTCNN_USED_DATA/negtive_face',
                                      positive_dir='/home/dataset/FDDB/MTCNN_USED_DATA/positive_face', batch=300)
            reg = BoundDataset(info='/home/dataset/FDDB/MTCNN_USED_DATA/part_info.txt', batch=300)
            c_trainop, c_train, c_testop, c_test = classifier.getIterator()
            r_trainop, r_train, r_testop, r_test = reg.getIterator()
            # init
            self.sess.run(tf.global_variables_initializer())
            self.model_saver.reload()
            self.sess.run([c_trainop, r_trainop, c_testop, r_testop])
            for i in range(0, 400):
                for step in tqdm(range(0, 20), file=sys.stdout):
                    x_c_train, y_c_train = self.sess.run(c_train)
                    x_r_train, y_r_train = self.sess.run(r_train)
                    X, YC, YR, label = merge(x_c_train, x_r_train, y_c_train, y_r_train)
                    self.sess.run([self.train_opt, self.add_step],
                                  feed_dict={self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label})
                x_c_train, y_c_train = self.sess.run(c_test)
                x_r_train, y_r_train = self.sess.run(r_test)
                X, YC, YR, label = merge(x_c_train, x_r_train, y_c_train, y_r_train)
                prob, g_step, acc, closs, bloss, comloss = self.sess.run(
                    [self.cls_prob, self.global_step, self.acc, self.cls_loss, self.bound_loss, self.com_loss],
                    feed_dict={self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label})
                self.model_saver.save(g_step)
                print('step {},classifier loss:{} acc {},bound loss:{},com loss:{}'.format(g_step, closs, acc, bloss,
                                                                                           comloss))

    def pred(self, img):
        with self.graph.as_default():
            self.model_saver.reload()
            prob = self.sess.run(self.face_c, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            br = self.sess.run(self.bound_r, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            return prob, br


class Onet:
    def __init__(self, is_train=True):
        self.graph = tf.Graph()
        with self.graph.as_default():
            self.x_input, self.face_c, self.bound_r, self.landmakr_r = Onet_factory()
            self.y_label = tf.placeholder(dtype=tf.float32, shape=[None, 2])
            self.y_bound = tf.placeholder(dtype=tf.float32, shape=[None, 4])
            self.y_landmark = tf.placeholder(dtype=tf.float32, shape=[None, 10])
            self.label = tf.placeholder(dtype=tf.float32, shape=[None, ])
            self.acc, self.cls_prob, self.cls_loss = Classifier_loss(self.label, self.y_label, self.face_c)
            self.bound_loss = Bounding_box_loss(self.label, self.bound_r, self.y_bound)
            self.landmark_loss = Landmark_loss(self.label, self.landmakr_r, self.y_landmark)
            self.com_loss = self.cls_loss * 1 + self.bound_loss * 0.5 + self.landmark_loss * 1
            if is_train:
                self.global_step = tf.Variable(0)
                self.add_step = tf.assign_add(self.global_step, 1)
                self.lr = tf.train.exponential_decay(learning_rate=0.01, global_step=self.global_step, decay_steps=1000,
                                                     decay_rate=0.5)
                self.train_opt = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.com_loss)
            self.sess = tf.Session()
            self.model_saver = ModelHelper(save_path='../../model/onet/', sess=self.sess)

    def train(self):
        from tfrecord.onet.DataSet import OnetDateSet
        with self.graph.as_default():
            od = OnetDateSet()
            # init
            self.sess.run(tf.global_variables_initializer())
            od.init(self.sess)
            self.model_saver.reload()

            for i in range(0, 400):

                for step in tqdm(range(0, 20), file=sys.stdout):
                    X, YC, YR, YL, label = od.get_data(self.sess)

                    self.sess.run([self.face_c,self.train_opt, self.add_step],
                                  feed_dict={self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label,self.y_landmark:YL})
                X, YC, YR, YL, label = od.get_data(sess=self.sess)
                ld_loss,prob, g_step, acc, closs, bloss, comloss = self.sess.run(
                    [self.landmark_loss,self.cls_prob, self.global_step, self.acc, self.cls_loss, self.bound_loss, self.com_loss],
                    feed_dict={self.y_landmark:YL,self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label})
                self.model_saver.save(g_step)
                print('step {},classifier loss:{:4} acc {:3},bound loss:{:4},landmark loss:{:4},com loss:{:4}'.format(g_step, closs, acc, bloss,ld_loss,comloss))

    def pred(self, img):
        if img.ndim ==3:
            img = np.reshape(img,[1,*img.shape])
        with self.graph.as_default():
            self.model_saver.reload()
            prob = self.sess.run(self.cls_prob, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            br = self.sess.run(self.bound_r, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            lm = self.sess.run(self.landmakr_r, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            return prob, br,lm


class Rnet:
    def __init__(self, is_train=True):
        self.graph = tf.Graph()
        with self.graph.as_default():
            self.x_input, self.face_c, self.bound_r, self.landmakr_r = Rnet_factory()
            self.y_label = tf.placeholder(dtype=tf.float32, shape=[None, 2])
            self.y_bound = tf.placeholder(dtype=tf.float32, shape=[None, 4])
            self.label = tf.placeholder(dtype=tf.float32, shape=[None, ])
            self.acc, self.cls_prob, self.cls_loss = Classifier_loss(self.label, self.y_label, self.face_c)
            self.bound_loss = Bounding_box_loss(self.label, self.bound_r, self.y_bound)
            self.com_loss = self.cls_loss * 2 + self.bound_loss
            if is_train:
                self.global_step = tf.Variable(0)
                self.add_step = tf.assign_add(self.global_step, 1)
                self.lr = tf.train.exponential_decay(learning_rate=0.01, global_step=self.global_step, decay_steps=1000,
                                                     decay_rate=0.5)
                self.train_opt = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.com_loss)
            self.sess = tf.Session()
            self.model_saver = ModelHelper(save_path='../../model/rnet/', sess=self.sess)

    def train(self):
        def merge(x_c_train, x_r_train, y_c_train, y_r_train):
            x_c_train, y_c_train = self.sess.run(c_train)
            x_r_train, y_r_train = self.sess.run(r_train)
            X = np.concatenate([x_c_train, x_r_train], axis=0)
            YC = np.concatenate([y_c_train, np.zeros(shape=[len(y_r_train), y_c_train.shape[1]])], axis=0)
            YR = np.concatenate([np.zeros(shape=[len(y_c_train), y_r_train.shape[1]]), y_r_train], axis=0)
            label = np.concatenate(
                [np.ones(shape=[len(x_c_train), ], dtype=int), np.zeros(shape=[len(x_r_train)], dtype=int)], axis=0)
            return X, YC, YR, label

        with self.graph.as_default():
            classifier = ImageDateSet(negative_dir='/home/dataset/FDDB/MTCNN_USED_DATA/negtive_face',
                                      positive_dir='/home/dataset/FDDB/MTCNN_USED_DATA/positive_face', batch=300)
            reg = BoundDataset(info='/home/dataset/FDDB/MTCNN_USED_DATA/part_info.txt', batch=300)
            c_trainop, c_train, c_testop, c_test = classifier.getIterator()
            r_trainop, r_train, r_testop, r_test = reg.getIterator()
            # init
            self.sess.run(tf.global_variables_initializer())
            self.model_saver.reload()
            self.sess.run([c_trainop, r_trainop, c_testop, r_testop])
            for i in range(0, 400):
                for step in tqdm(range(0, 20), file=sys.stdout):
                    x_c_train, y_c_train = self.sess.run(c_train)
                    x_r_train, y_r_train = self.sess.run(r_train)
                    X, YC, YR, label = merge(x_c_train, x_r_train, y_c_train, y_r_train)
                    self.sess.run([self.train_opt, self.add_step],
                                  feed_dict={self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label})
                x_c_train, y_c_train = self.sess.run(c_test)
                x_r_train, y_r_train = self.sess.run(r_test)
                X, YC, YR, label = merge(x_c_train, x_r_train, y_c_train, y_r_train)
                prob, g_step, acc, closs, bloss, comloss = self.sess.run(
                    [self.cls_prob, self.global_step, self.acc, self.cls_loss, self.bound_loss, self.com_loss],
                    feed_dict={self.x_input: X, self.y_label: YC, self.y_bound: YR, self.label: label})
                self.model_saver.save(g_step)
                print('step {},classifier loss:{} acc {},bound loss:{},com loss:{}'.format(g_step, closs, acc, bloss,
                                                                                           comloss))

    def pred(self, img):
        with self.graph.as_default():
            self.model_saver.reload()
            prob = self.sess.run(self.face_c, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            br = self.sess.run(self.bound_r, feed_dict={self.x_input: img, self.label: np.ones(shape=[len(img)])})
            return prob, br


class MTCNN:
    def __init__(self):
        pass


import cv2


def test_model():
    from util import drwa_bbox, NMS, img_pyramids
    import matplotlib.pyplot as plt
    img = plt.imread('/home/dataset/FDDB/2002/07/19/big/img_18.jpg')
    pyramids, imgs_win, bbox = img_pyramids(img, pyramcount=2, winsize=(96, 96), step=(10, 10))
    x_pred = []
    for i in imgs_win:
        # cv2.imshow('a',i)
        # cv2.waitKey()
        x_pred.append(cv2.resize(i, dsize=(12, 12)))
    x_pred = np.array(x_pred)
    print(x_pred.shape)
    prob, br = Pnet(is_train=False).pred(x_pred)
    cal_bbx = []
    for i in range(0, len(br)):
        cal_x, cal_y, cal_w, cal_h = br[i]
        xb, yb, wb, hb = bbox[i]
        cal_bbx.append([xb + cal_x, yb + cal_y, wb * cal_w, hb * cal_h])
    prob = prob[:, 1]
    print(br)
    imgs = np.array(imgs_win, dtype=int)
    idx = np.where(prob > 0.95)[0]
    bb, bidxs = NMS(np.array(cal_bbx)[idx], prob[idx], 0.5)
    img = drwa_bbox(img, np.array(bbox)[idx])
    img = drwa_bbox(img, np.array(cal_bbx)[idx], color=(0, 255, 0))
    plt.imshow(img)
    plt.show()
    for i in idx:
        plt.imshow(imgs[i])
        plt.show()

def test_onet():
    import matplotlib.pyplot as plt
    import cv2

    file = '009.jpg'
    img = plt.imread(file)
    img = cv2.resize(img,dsize=(48,48))
    plt.imshow(img)
    plt.show()
    net = Onet()
    prob,bb,lm = net.pred(img)
    print(prob[0],bb[0])
    h,w,c = img.shape
    lm=lm[0]
    for i in range(0,len(lm),2):
        x1 = int(lm[i] * w)
        y1 = int(lm[i+1] * h)
        img = cv2.circle(img,(x1,y1),2,color=(255,0,0))
    plt.imshow(img)
    plt.show()

if __name__ == '__main__':
    # # pnet().train()
    # Onet().train()
    test_onet()
    pass