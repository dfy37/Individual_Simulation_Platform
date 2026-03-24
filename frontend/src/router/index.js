import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import SetupView from '../views/SetupView.vue'
import SimulationView from '../views/SimulationView.vue'
import OnlineSimView from '../views/OnlineSimView.vue'
import InterviewView from '../views/InterviewView.vue'

const routes = [
  { path: '/',            name: 'Home',       component: HomeView },
  { path: '/setup',       name: 'Setup',      component: SetupView },
  { path: '/simulation',  name: 'Simulation', component: SimulationView },
  { path: '/online-sim',  name: 'OnlineSim',  component: OnlineSimView },
  { path: '/interview',   name: 'Interview',  component: InterviewView },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
