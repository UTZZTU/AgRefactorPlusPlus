#include <vector>
#include <set>

using namespace std;

struct TreeNode {
    int val;
    TreeNode *left;
    TreeNode *right;
    TreeNode() : val(0), left(nullptr), right(nullptr) {}
    TreeNode(int x) : val(x), left(nullptr), right(nullptr) {}
    TreeNode(int x, TreeNode *left, TreeNode *right) : val(x), left(left), right(right) {}
};

TreeNode* helper(TreeNode* node, vector<TreeNode*>& result, set<int>& to_delete_set, bool is_root) {
    if (node == nullptr) return nullptr;
    bool deleted = to_delete_set.find(node->val) != to_delete_set.end();
    if (is_root && !deleted) result.push_back(node);
    node->left = helper(node->left, result, to_delete_set, deleted);
    node->right =  helper(node->right, result, to_delete_set, deleted);
    return deleted ? nullptr : node;
}

vector<TreeNode*> delNodes(TreeNode* root, vector<int>& to_delete) {
    vector<TreeNode*> result;
    set<int> to_delete_set;
    for (int i : to_delete)
        to_delete_set.insert(i);
    helper(root, result, to_delete_set, true);
    return result;
}
