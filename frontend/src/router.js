import { createRouter, createWebHistory } from 'vue-router'
import Login from './components/Login.vue'
import Setup from './components/Setup.vue'
import LiveDashboard from './components/LiveDashboard.vue'
import MetricsView from './components/MetricsView.vue'
import HistoryView from './components/HistoryView.vue'
import SpaceEditor from './components/SpaceEditor.vue'
import ParkingLayoutEditor from './components/ParkingLayoutEditor.vue'
import CameraList from './components/CameraList.vue'
import Settings from './components/Settings.vue'
import UserManagement from './components/UserManagement.vue'
import APISection from './components/APISection.vue'
import StorageDiagnostics from './components/StorageDiagnostics.vue'
import HeapDiagnostics from './components/HeapDiagnostics.vue'
import StreamDiagnostics from './components/StreamDiagnostics.vue'
import LogViewer from './components/LogViewer.vue'
import ThreadDiagnostics from './components/ThreadDiagnostics.vue'
import DiagnosticsMenu from './components/DiagnosticsMenu.vue'
import Alerts from './components/Alerts.vue'

const routes = [
  { path: '/', component: Login },
  { path: '/setup', component: Setup },
  { path: '/cameras', component: CameraList },
  { path: '/dashboard', component: LiveDashboard },
  { path: '/layout', component: ParkingLayoutEditor, meta: { requiresPermission: 'manage_cameras' } },
  { path: '/metrics/:cameraId?', component: MetricsView, props: true },
  { path: '/metrics', component: MetricsView },
  { path: '/history', component: HistoryView },
  { path: '/editor/:cameraId', component: SpaceEditor, props: true },
  { path: '/settings', component: Settings },
  { path: '/users', component: UserManagement },
  { path: '/access', component: APISection },
  { path: '/diagnostics', component: DiagnosticsMenu, meta: { requiresAdmin: true } },
  { path: '/diagnostics/storage', component: StorageDiagnostics, meta: { requiresAdmin: true } },
  { path: '/diagnostics/heap', component: HeapDiagnostics, meta: { requiresAdmin: true } },
  { path: '/diagnostics/streams', component: StreamDiagnostics, meta: { requiresAdmin: true } },
  { path: '/diagnostics/logs', component: LogViewer, meta: { requiresAdmin: true } },
  { path: '/diagnostics/threads', component: ThreadDiagnostics, meta: { requiresAdmin: true } },
  { path: '/alerts', component: Alerts }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  const publicPages = ['/', '/setup']
  const authRequired = !publicPages.includes(to.path)
  const loggedIn = localStorage.getItem('token')
  const isAdmin = localStorage.getItem('is_admin') === 'true'
  // Guarded JSON.parse: a corrupted permissions value would otherwise
  // throw inside the navigation guard and block ALL navigation (blank
  // page). App.vue.hasPermission does the same defensive parse.
  let permissions = []
  try {
    permissions = JSON.parse(localStorage.getItem('permissions') || '[]')
  } catch (_) {
    permissions = []
  }

  if (authRequired && !loggedIn) {
    return next('/')
  }

  // Admin override allows all routes
  if (isAdmin) return next();

  // Check route permissions.
  if (to.meta.requiresPermission && !permissions.includes(to.meta.requiresPermission)) {
    console.warn("[AUTH] Missing permission for route:", to.path)
    return next('/dashboard')
  }

  // Admin route protection for non-admins
  if (to.meta.requiresAdmin && !permissions.includes('view_diagnostics')) {
    console.warn("[AUTH] Unauthorized access attempt to diagnostic route:", to.path)
    return next('/dashboard') // Redirect to dashboard as safe default
  }

  next()
})

export default router
