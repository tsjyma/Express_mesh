### 1. Introduction

介绍 motivation (100\~150)

我们做了什么 (150)

### 2. Architecture

#### 2.1. Topology \& Constraints

基本 mesh (20\~50)

physical constraints, 说一下主实验用的 express 都是 ideal latency = 1, 但是后面会补充 length aware 的. (50\~100)

#### 2.2. Placement Algorithms

##### 2.2.1. Random

(20)

##### 2.2.2. ASPL Greedy

简述思想 (20\~40)

伪代码

##### 2.2.3. Simulated Annealing

简述思想，注意 SA 是依赖于后面的 routing algorithm 的 (30\~50)

提一下我们实现了一个 standalone（实际上你讲的时候不要用这个名字，不然老师看不懂，也就是我们实现了一个轻量化的复现garnet的代码，用于evaluate placement）

伪代码

这里注意伪代码不要太specific。比如说有一些参数的设置，或者有一些机制，你就用一些未知数来代替（包括后面q和r的参数），不要让老师看出来，我们是为了实验效果好看fine-tune过的，尽量写general的代码框架

#### 2.3. Routing Algorithm

##### 2.3.1. 核心算法

核心：在一开始就选定路线 (20)

路线候选：mesh XY + top-K，这样的道理何在 (50)

实际选择：讲 q 和 r 的定义，道理何在 (50)

##### 2.3.2. 所需信息传递机制

讲一下 q 和 r 和传递机制 (50\~100)

##### 2.3.3. Deadlock Prevention

讲 escape routing 和证明 (100)

### 3. Implementation

这部分你先自由发挥，可以参考 去年的好的project的例子。

### 4. Evaluation

#### 4.1. Traffics

讲实验中用到的这些 traffics 的数学描述 (50)

#### 4.2. 静态数据 \& visualization

放个表格简单文字讲一下要点 (20\~40)

#### 4.3. 基本配置

讲一下基本配置，包括 garnet 默认的，例如 VC 数量之类。可以用一个表格列出

#### 4.4. 主实验

##### 4.4.1. 扫 inj rate 的曲线

先说一下每个traffic的greedy和sa都是traffic-aware的.

放 0.4/0.7/0.8 的三张表上去，并写上 random 相比 mesh, greedy 相比 random, SA 相比 greedy 的 improvement percentage

然后放那些曲线图

简单说一下结论 (50)

##### 4.4.2. distribution

放那个 random topology 的 throughput distribution 然后画有 greedy 和 SA 的线的图，文字说一下优于多少 percent (20)

##### 4.4.3. link load heatmap

##### 4.4.4. cross matrix

#### 4.5. Ablation Study

##### 4.5.1. Routing Ablation

主要为了体现我们努力尝试了其他的一些算法

结论：dijkstra 确实更优但运算量太大不现实；其他几种更弱的算法都不行；mesh 上 adaptive 反而由于只看了局部信息，不优。(50\~100)

##### 4.5.2. Information Ablation

结论：我们的信息传播机制更符合物理实际，并且实际上表现和理想瞬间传递信息的设定非常相近。(50\~70)

##### 4.5.3. Escape Timeout Sweep

结论：太小的不行，增大到 critical point 之后缓慢 degrade。(20\~50)

##### 4.5.4. Escape Channel Eliminates Deadlock

文字讲一下什么配置下 escape off 会有死锁，然后放上 (20\~40)

#### 4.6. Scaling Experiments

先讲清楚各个实验配置（有相关变量跟着 scale 的要说明合理性），给每个 row 一个有 semantics 的短命名，然后直接展示两张柱状统计图（row index 改成短命名） (100)

结论：在不同的配置下，我们设计的 greedy / SA placement 以及 routing 算法仍然维持优势。

### 5. Conclusion

总结一下我们的设计的效果（是好的），以及未来的方向：继续 scale, 更现实地考虑 express cost，更现实地考虑预规划路径的 metadata 负载，考虑有 wormhole 之后的规划。(100\~200)

