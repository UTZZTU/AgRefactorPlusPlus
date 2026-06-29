#include <vector>

using namespace std;

int timer= 1;

void dfs(int node , int parent,vector<int>& tin,vector<int>& low,vector<int>& vis, vector<vector<int>>& bridges,vector<int> adj[]){
    vis[node] = 1;
    tin[node] = low[node] = timer++;
    // Traverse all nodes
    for(auto it : adj[node]){
        if(it == parent) continue;
        // Not visited ? check for bridge
        if(!vis[it]){
            dfs(it,node,tin,low,vis,bridges,adj);
            low[node] = min(low[node],low[it]);
            if(tin[node] < low[it]){
                bridges.push_back({it,node});
            }
        }
        // visited ? No chance for a bridge , update the low
        else {
            low[node] = min(low[node],tin[it]);
        }
    }
}

vector<vector<int>> criticalConnections(int n, vector<vector<int>>& connections) {
    vector<int> adj[n] , vis(n,0) , tin(n) , low(n);
    vector<vector<int>> bridges;
    // Form an adjacent List
    for(auto it : connections){
        adj[it[0]].push_back(it[1]);
        adj[it[1]].push_back(it[0]);
    }

    dfs(0,-1,tin,low,vis,bridges,adj);
    
    return bridges;
}